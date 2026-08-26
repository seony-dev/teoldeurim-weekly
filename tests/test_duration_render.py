# -*- coding: utf-8 -*-
"""(2026-08-26) MAX_DURATION_SEC=None HTML 렌더 버그 회귀 테스트.

배경:
  묘한덕질 / 짤덕방 target 은 MAX_DURATION_SEC=None (상한 없음). 이전 렌더는
  `{max_dur}초 이하` 를 무조건 문자열 삽입해 "None초 이하" 라고 잘못 표시되던
  버그가 있었음.

지키는 원칙:
  · min=15, max=180  → "15초 이상 ~ 180초 이하 (Shorts 형식)"    (털어드림)
  · min=20, max=None → "20초 이상 (상한 없음)"                    (묘한덕질/짤덕방)
  · min=0,  max=180  → "180초 이하 (Shorts 형식)"                  (하한 미설정)
  · min=0,  max=None → "제한 없음"                                (양쪽 미설정)
  · 실제 Hard Filter 조건 (hard_filter 함수) 은 변경하지 않음.
  · 렌더 함수(_render_duration_label) 는 실제 HTML 필터 기준 <li> 에 사용됨.

실행:
  python tests/test_duration_render.py
"""
import sys
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

for m in ["benchmark", "benchmark_config", "targets",
          "targets.teoldeurim", "targets.myohanduk", "targets.jjalduk"]:
    if m in sys.modules:
        del sys.modules[m]

import benchmark
from targets import resolve_mode

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


# ═══════════════════════════════════════════════════════════════════
# 1) _render_duration_label 헬퍼 직접 검증
# ═══════════════════════════════════════════════════════════════════
_r = benchmark._render_duration_label

chk("HELPER-01: 털어드림 (15, 180) → '15초 이상 ~ 180초 이하 (Shorts 형식)'",
    _r(15, 180) == "15초 이상 ~ 180초 이하 (Shorts 형식)",
    f"got {_r(15, 180)!r}")
chk("HELPER-02: 묘한덕질/짤덕방 (20, None) → '20초 이상 (상한 없음)'",
    _r(20, None) == "20초 이상 (상한 없음)",
    f"got {_r(20, None)!r}")
chk("HELPER-03: (0, 180) → '180초 이하 (Shorts 형식)' (하한 없음)",
    _r(0, 180) == "180초 이하 (Shorts 형식)",
    f"got {_r(0, 180)!r}")
chk("HELPER-04: (0, None) → '제한 없음'",
    _r(0, None) == "제한 없음",
    f"got {_r(0, None)!r}")
# 이전 버그 재현 방지 assertion
chk("HELPER-05: 결과에 'None초' 문자열 없음 (버그 원인 재현 방지)",
    "None초" not in _r(20, None)
    and "None초" not in _r(0, None)
    and "None초" not in _r(15, 180),
    "None초 여전히 발견")


# ═══════════════════════════════════════════════════════════════════
# 2) render_report 실 HTML 에 정확한 문구가 삽입되는지 (통합 검증)
# ═══════════════════════════════════════════════════════════════════
def _fake_config_snapshot(target_slug, mode):
    """실제 실행 없이 config_snapshot 재현."""
    import benchmark_config
    cfg = resolve_mode(target_slug, mode)
    for k, v in benchmark_config.BENCHMARK_CONFIG.items():
        cfg.setdefault(k, v)
    return {
        "MAX_VIEWS": cfg.get("MAX_VIEWS", 0),
        "MIN_VIEWS": cfg.get("MIN_VIEWS", 0),
        "MAX_SHORTS_PER_CHANNEL": cfg.get("MAX_SHORTS_PER_CHANNEL", 0),
        # 신규 로직: None 그대로 저장
        "MAX_DURATION_SEC": cfg.get("MAX_DURATION_SEC"),
        "MIN_DURATION_SEC": cfg.get("MIN_DURATION_SEC") or 0,
        "MIN_AGE_DAYS_EXCLUSIVE": cfg.get("MIN_AGE_DAYS_EXCLUSIVE"),
        "UPLOADED_WITHIN_DAYS": cfg.get("UPLOADED_WITHIN_DAYS", 0),
        "MAX_ANALYSIS_CANDIDATES": cfg.get("MAX_ANALYSIS_CANDIDATES", 0),
        "FINAL_CANDIDATES": cfg.get("FINAL_CANDIDATES", 0),
        "EXCLUDE_CHANNELS": sorted(cfg.get("EXCLUDE_CHANNELS", set())),
    }


# 빈 stage_data 로 render_report 실행 (HTML 생성 자체는 통과) 후 필터 기준 <li> 검증
def _empty_stage():
    return {
        "collected": [], "max_views_excluded": [], "hard_excluded": [],
        "analyzed": [], "sent_excluded": [], "direct_final": [],
        "benchmark_final": [], "final": [],
    }


cases = [
    ("teoldeurim", "weekly",   "15초 이상 ~ 180초 이하 (Shorts 형식)"),
    ("teoldeurim", "standard", "15초 이상 ~ 180초 이하 (Shorts 형식)"),
    ("myohanduk",  "weekly",   "20초 이상 (상한 없음)"),
    ("jjalduk",    "weekly",   "20초 이상 (상한 없음)"),
]

for slug, mode, expected in cases:
    from targets import get_target
    t = get_target(slug)
    snap = _fake_config_snapshot(slug, mode)
    html = benchmark.render_report(
        _empty_stage(), {"direct_common_patterns":[],
                        "benchmark_common_patterns":[],
                        "per_channel_insights":[]},
        "2026-08-28", ref_channels=t["reference_channels"],
        config_used=snap, profile=mode, target=t,
    )
    tag = f"RENDER[{slug}/{mode}]: '{expected}'"
    chk(tag, expected in html, f"expected substring missing")

# 이전 버그 재현 방지: 어떤 HTML 에도 "None초" 없어야
_any_none_ch = False
for slug, mode, _ in cases:
    from targets import get_target
    t = get_target(slug)
    snap = _fake_config_snapshot(slug, mode)
    html = benchmark.render_report(
        _empty_stage(), {"direct_common_patterns":[],
                        "benchmark_common_patterns":[],
                        "per_channel_insights":[]},
        "2026-08-28", ref_channels=t["reference_channels"],
        config_used=snap, profile=mode, target=t,
    )
    if "None초" in html:
        _any_none_ch = True
        break
chk("BUG-01: 네 target/mode 모두 렌더 결과에 'None초' 문자열 없음 (버그 재현 방지)",
    not _any_none_ch)


# ═══════════════════════════════════════════════════════════════════
# 3) Hard Filter 실 판정은 무변경 (렌더 버그 수정이 판정에 영향 X)
# ═══════════════════════════════════════════════════════════════════
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc)


def _mk(dur=30, days_ago=1, views=200_000):
    return {
        "video_id": "x", "title": "t", "channel_name": "ref", "channel_id": "u",
        "views": views, "likes": 0, "comments": 0, "subscribers": 1,
        "duration_sec": dur,
        "published_at": (now - timedelta(days=days_ago)).isoformat(),
        "url": "https://youtu.be/x",
    }


def _run(slug, mode, dur, days_ago=1, views=None):
    import benchmark_config
    cfg = resolve_mode(slug, mode)
    for k, v in benchmark_config.BENCHMARK_CONFIG.items():
        cfg.setdefault(k, v)
    benchmark.CFG.clear(); benchmark.CFG.update(cfg)
    # standard mode 는 조회수 하한 500k · age >30d 필요 — 인자로 override
    _views = views if views is not None else (600_000 if mode == "standard" else 200_000)
    _days = 60 if mode == "standard" else days_ago
    passed, _ = benchmark.hard_filter([_mk(dur, days_ago=_days, views=_views)], now)
    return len(passed) == 1


# 묘한덕질/짤덕방 (max_dur=None) — 181초, 600초도 통과해야 하는 것 재확인
chk("FILTER-JJ-1: 짤덕방 (max=None) 181초 PASS",
    _run("jjalduk", "weekly", 181))
chk("FILTER-JJ-2: 짤덕방 600초도 PASS (상한 없음)",
    _run("jjalduk", "weekly", 600))
chk("FILTER-JJ-3: 짤덕방 19초 FAIL (min 20)",
    not _run("jjalduk", "weekly", 19))
chk("FILTER-MH-1: 묘한덕질 181초 PASS",
    _run("myohanduk", "weekly", 181))
chk("FILTER-TE-1: 털어드림 weekly 181초 FAIL (max 180)",
    not _run("teoldeurim", "weekly", 181))
chk("FILTER-TE-2: 털어드림 standard 180초 PASS (inclusive)",
    _run("teoldeurim", "standard", 180))


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" MAX_DURATION_SEC=None HTML 렌더 · Hard Filter 무변경 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
