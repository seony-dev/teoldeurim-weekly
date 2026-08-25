# -*- coding: utf-8 -*-
"""털어드림 target profile 이동 회귀 테스트 (2026-08-25 리팩터링).

지키는 원칙:
  · Weekly/Standard 각각의 hard filter 경계 값이 사용자 요구사항과 일치.
    - Weekly: 조회수 50,000~3,000,000 / 길이 15~180초 / 업로드 0~7일
    - Standard: 조회수 500,000~9,000,000 / 길이 15~180초 / 업로드 30d exclusive~365d
  · MAX_VIEWS 상한은 모두 inclusive (3M PASS / 3M+1 FAIL, 9M PASS / 9M+1 FAIL)
  · Standard age boundary: exact 30d FAIL, 30d+1s PASS, exact 365d PASS, 365d+1s FAIL
  · Weekly age boundary: exact 7d PASS, 7d+1s FAIL
  · VIDEO_SCHEMA 는 target_angle 필드 사용 (teoldeurim_angle 은 read-legacy 만).
  · _target_angle() 이 legacy teoldeurim_angle 도 fallback 지원.
  · analyze_video/analyze_patterns 는 이제 target 인자 필수.

실행:
  프로젝트 루트에서:  python tests/test_target_teoldeurim_regression.py
  종료 코드: 성공 0 / 실패 1
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

# 매 실행 fresh import (모듈 캐시가 이전 세션 값을 물고 있으면 CFG 오염 위험)
for m in ["benchmark", "benchmark_config", "targets", "targets.teoldeurim"]:
    if m in sys.modules:
        del sys.modules[m]

import benchmark
import benchmark_config
from targets import get_target, resolve_mode

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))

now = datetime.now(timezone.utc)


def _mk_video(views=100_000, duration=30, days_ago=1, seconds_extra=0,
              title="테스트", vid="v001"):
    pub = now - timedelta(days=days_ago, seconds=seconds_extra)
    return {
        "video_id": vid, "title": title,
        "channel_name": "ref_channel", "channel_id": "UCref",
        "views": views, "likes": 0, "comments": 0, "subscribers": 1,
        "duration_sec": duration,
        "published_at": pub.isoformat(),
        "url": f"https://youtu.be/{vid}",
    }


def _run_filter(mode, video):
    """CFG 를 target=teoldeurim + mode 로 세팅 후 hard_filter/apply_max_views 검사."""
    cfg = resolve_mode("teoldeurim", mode)
    for k, v in benchmark_config.BENCHMARK_CONFIG.items():
        cfg.setdefault(k, v)
    benchmark.CFG.clear()
    benchmark.CFG.update(cfg)
    under_max, over_max = benchmark.apply_max_views_filter([video])
    if not under_max:
        return "MAX_VIEWS_CUT"
    passed, excluded = benchmark.hard_filter(under_max, now)
    if passed:
        return "PASS"
    return f"HARD_CUT[{excluded[0][1]}]"


# ═══════════════════════════════════════════════════════════════════
# 1) target 레지스트리 · profile 이동
# ═══════════════════════════════════════════════════════════════════
t = get_target("teoldeurim")
chk("REG-01: target slug=teoldeurim",
    t["slug"] == "teoldeurim")
chk("REG-02: display_name = '털어드림'",
    t["display_name"] == "털어드림")
chk("REG-03: reference_channels 7개",
    len(t["reference_channels"]) == 7)
chk("REG-04: modes 에 weekly + standard 모두 존재",
    set(t["modes"].keys()) == {"weekly", "standard"})
chk("REG-05: angle_field_label = 털어드림식 변형 각도",
    t["identity"]["angle_field_label"] == "털어드림식 변형 각도")

# analyze_video_system_prompt 안에 원본 identity 문구 남아있는지 (회귀 방지)
sp = t["identity"]["analyze_video_system_prompt"]
chk("REG-06: system prompt 에 '털어드림' 정체성 유지",
    "털어드림" in sp and "K-pop" in sp and "1군" in sp)
chk("REG-07: system prompt 에 8소재 유형 헤더 유지",
    "8가지 핵심 소재 유형" in sp)
chk("REG-08: system prompt 에 5훅 패턴 헤더 유지",
    "5가지 훅 패턴" in sp)


# ═══════════════════════════════════════════════════════════════════
# 2) Weekly hard filter 경계 (49,999 / 50k / 3M / 3M+1 / 15s / 180s / 7d / 7d+1s)
# ═══════════════════════════════════════════════════════════════════
_W = "weekly"
chk("WK-VIEWS-01: 49,999 views → FAIL",
    _run_filter(_W, _mk_video(views=49_999)) != "PASS")
chk("WK-VIEWS-02: 50,000 views → PASS",
    _run_filter(_W, _mk_video(views=50_000)) == "PASS")
chk("WK-VIEWS-03: 3,000,000 views → PASS (inclusive)",
    _run_filter(_W, _mk_video(views=3_000_000)) == "PASS")
chk("WK-VIEWS-04: 3,000,001 views → MAX_VIEWS_CUT",
    _run_filter(_W, _mk_video(views=3_000_001)) == "MAX_VIEWS_CUT")

chk("WK-DUR-01: 14 sec → FAIL",
    _run_filter(_W, _mk_video(duration=14)) != "PASS")
chk("WK-DUR-02: 15 sec → PASS",
    _run_filter(_W, _mk_video(duration=15)) == "PASS")
chk("WK-DUR-03: 180 sec → PASS (inclusive)",
    _run_filter(_W, _mk_video(duration=180)) == "PASS")
chk("WK-DUR-04: 181 sec → FAIL",
    _run_filter(_W, _mk_video(duration=181)) != "PASS")

# exact 7d = PASS (age <= 7d inclusive)
chk("WK-AGE-01: exact 7d → PASS",
    _run_filter(_W, _mk_video(days_ago=7, seconds_extra=0)) == "PASS")
chk("WK-AGE-02: 7d + 1s → FAIL",
    _run_filter(_W, _mk_video(days_ago=7, seconds_extra=1)) != "PASS")


# ═══════════════════════════════════════════════════════════════════
# 3) Standard hard filter 경계
# ═══════════════════════════════════════════════════════════════════
_S = "standard"
chk("STD-VIEWS-01: 499,999 → FAIL",
    _run_filter(_S, _mk_video(views=499_999, days_ago=60)) != "PASS")
chk("STD-VIEWS-02: 500,000 → PASS",
    _run_filter(_S, _mk_video(views=500_000, days_ago=60)) == "PASS")
chk("STD-VIEWS-03: 9,000,000 → PASS (inclusive)",
    _run_filter(_S, _mk_video(views=9_000_000, days_ago=60)) == "PASS")
chk("STD-VIEWS-04: 9,000,001 → MAX_VIEWS_CUT",
    _run_filter(_S, _mk_video(views=9_000_001, days_ago=60)) == "MAX_VIEWS_CUT")

# Standard age: exact 30d = FAIL, 30d+1s = PASS
chk("STD-AGE-01: exact 30d → FAIL",
    _run_filter(_S, _mk_video(views=1_000_000, days_ago=30, seconds_extra=0)) != "PASS")
chk("STD-AGE-02: 30d + 1s → PASS",
    _run_filter(_S, _mk_video(views=1_000_000, days_ago=30, seconds_extra=1)) == "PASS")
# exact 365d = PASS, 365d+1s = FAIL
chk("STD-AGE-03: exact 365d → PASS",
    _run_filter(_S, _mk_video(views=1_000_000, days_ago=365, seconds_extra=0)) == "PASS")
chk("STD-AGE-04: 365d + 1s → FAIL",
    _run_filter(_S, _mk_video(views=1_000_000, days_ago=365, seconds_extra=1)) != "PASS")


# ═══════════════════════════════════════════════════════════════════
# 4) VIDEO_SCHEMA target_angle + legacy teoldeurim_angle read-compat
# ═══════════════════════════════════════════════════════════════════
schema = benchmark.VIDEO_SCHEMA
chk("SCH-01: VIDEO_SCHEMA required 에 target_angle 포함",
    "target_angle" in schema["required"])
chk("SCH-02: VIDEO_SCHEMA required 에 teoldeurim_angle 미포함",
    "teoldeurim_angle" not in schema["required"])

# _target_angle 헬퍼: legacy fallback
_legacy = {"teoldeurim_angle": "레거시 값"}
_new = {"target_angle": "신규 값"}
_both = {"target_angle": "신규 우선", "teoldeurim_angle": "무시됨"}
chk("SCH-03: _target_angle legacy 필드도 fallback 지원",
    benchmark._target_angle(_legacy) == "레거시 값")
chk("SCH-04: _target_angle 신규 필드 우선",
    benchmark._target_angle(_new) == "신규 값")
chk("SCH-05: _target_angle 신규+legacy 병존 시 신규 우선",
    benchmark._target_angle(_both) == "신규 우선")
chk("SCH-06: _target_angle 빈 dict → 빈 문자열",
    benchmark._target_angle({}) == "")


# ═══════════════════════════════════════════════════════════════════
# 5) analyze_video / analyze_patterns signature — target 인자 필수
# ═══════════════════════════════════════════════════════════════════
import inspect
av = inspect.signature(benchmark.analyze_video)
ap = inspect.signature(benchmark.analyze_patterns)
chk("SIG-01: analyze_video 는 (client, v, target) 시그니처",
    list(av.parameters.keys()) == ["client", "v", "target"])
chk("SIG-02: analyze_patterns 는 (client, candidates, target) 시그니처",
    list(ap.parameters.keys()) == ["client", "candidates", "target"])


# ═══════════════════════════════════════════════════════════════════
# 6) build_reference_channels — 7개 반환, 자동 추출 제거
# ═══════════════════════════════════════════════════════════════════
refs = benchmark.build_reference_channels(t)
chk("REF-01: 참고 채널 7개",
    len(refs["merged"]) == 7)
chk("REF-02: 모든 채널 _auto=False",
    all(r.get("_auto") is False for r in refs["merged"]))
chk("REF-03: 반환 dict 에 auto_discovered_reference_candidates 없음 (기능 제거)",
    "auto_discovered_reference_candidates" not in refs)
# 특정 채널 이름 존재 확인
names = {r["name"] for r in refs["merged"]}
for expected in ["패션탐정냥", "덕칼럼", "팝픽", "아이소",
                 "초미녀 갤러리", "케이팝과몰입러", "돌아에몽"]:
    chk(f"REF-04[{expected}]: 참고 채널 목록에 포함",
        expected in names)


# ═══════════════════════════════════════════════════════════════════
# 7) sent history loader — target namespaced + legacy backward-compat
# ═══════════════════════════════════════════════════════════════════
# 실제 fs 은 건드리지 않고 legacy_flat / legacy_weekly 플래그만 확인
chk("HIS-01: 털어드림 legacy_sent_history_flat = True",
    t.get("legacy_sent_history_flat") is True)
chk("HIS-02: 털어드림 legacy_weekly_history = True",
    t.get("legacy_weekly_history") is True)
chk("HIS-03: history_shared_across_modes = True (weekly ↔ standard 공유)",
    t.get("history_shared_across_modes") is True)
chk("HIS-04: history_namespace = teoldeurim",
    t.get("history_namespace") == "teoldeurim")

# 실제 legacy 파일 존재 시 loader 가 물리적 파일을 건드리지 않는지 (읽기 전용)
ns, flat, weekly, merged = benchmark.load_sent_video_ids_for_target(t)
chk("HIS-05: loader 반환 4-tuple 형태",
    isinstance(ns, set) and isinstance(flat, set)
    and isinstance(weekly, set) and isinstance(merged, set))
chk("HIS-06: merged = ns ∪ flat ∪ weekly 정확",
    merged == (ns | flat | weekly))
# legacy flat 파일이 실제로 있다면 loader 가 최소 1개 이상 읽어야 함
_flat_dir = ROOT / "benchmark" / "history" / "sent"
_has_flat_files = _flat_dir.exists() and any(
    f.name.endswith("_recent.json") or f.name.endswith("_standard.json")
    for f in _flat_dir.iterdir() if f.is_file()
)
if _has_flat_files:
    chk("HIS-07: legacy flat 파일이 존재하면 loader 결과 non-empty",
        len(flat) > 0)
_weekly_dir = ROOT / "history"
_has_weekly_files = _weekly_dir.exists() and any(_weekly_dir.glob("*.json"))
if _has_weekly_files:
    chk("HIS-08: legacy weekly history 존재하면 loader 결과 non-empty",
        len(weekly) > 0)


# ═══════════════════════════════════════════════════════════════════
# 8) MAX_ANALYSIS / FINAL 값 검증
# ═══════════════════════════════════════════════════════════════════
w = t["modes"]["weekly"]; s = t["modes"]["standard"]
chk("CAP-01: teoldeurim weekly MAX_ANALYSIS = 30",
    w["MAX_ANALYSIS_CANDIDATES"] == 30)
chk("CAP-02: teoldeurim weekly FINAL = 5 (최종 통일 · direct/benchmark 각각 cap)",
    w["FINAL_CANDIDATES"] == 5)
chk("CAP-03: teoldeurim standard MAX_ANALYSIS = 30",
    s["MAX_ANALYSIS_CANDIDATES"] == 30)
chk("CAP-04: teoldeurim standard FINAL = 5 (최종 통일 · direct/benchmark 각각 cap)",
    s["FINAL_CANDIDATES"] == 5)


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 털어드림 target profile 이동 회귀 결과")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
