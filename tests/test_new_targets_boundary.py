# -*- coding: utf-8 -*-
"""묘한덕질 / 짤덕방 신규 target hard filter · dedup 격리 회귀.

지키는 원칙 (사용자 확정 조건):
  · 조회수 100,000 이상 ~ 3,000,000 이하 (양단 inclusive)
  · 길이 20초 이상 (상한 없음 — MAX_DURATION_SEC=None)
  · 업로드 직후 ~ 7일 이내
  · '180초 max' 는 이번 요구사항에 없다. 실수로 적용되지 않았는지 검증.
  · target 별 sent history namespace 격리:
      - 짤덕방 legacy 미참조 (털어드림 history 무시)
      - 묘한덕질 legacy 미참조 (털어드림 history 무시)

실행:
  python tests/test_new_targets_boundary.py
"""
import sys
from datetime import datetime, timezone, timedelta
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
import benchmark_config
from targets import get_target, resolve_mode

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))

now = datetime.now(timezone.utc)


def _mk_video(views=200_000, duration=30, days_ago=1, seconds_extra=0, vid="v1"):
    pub = now - timedelta(days=days_ago, seconds=seconds_extra)
    return {
        "video_id": vid, "title": "테스트",
        "channel_name": "ref", "channel_id": "UCref",
        "views": views, "likes": 0, "comments": 0, "subscribers": 1,
        "duration_sec": duration,
        "published_at": pub.isoformat(),
        "url": f"https://youtu.be/{vid}",
    }


def _run_filter(target_slug, mode, video):
    cfg = resolve_mode(target_slug, mode)
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
# 1) 짤덕방 boundary (views 100k~3M inclusive, 20s min, no max dur, 7d age)
# ═══════════════════════════════════════════════════════════════════
tgt = "jjalduk"; md = "weekly"
chk("JJ-VIEWS-01: 99,999 → FAIL",
    _run_filter(tgt, md, _mk_video(views=99_999)) != "PASS")
chk("JJ-VIEWS-02: 100,000 → PASS",
    _run_filter(tgt, md, _mk_video(views=100_000)) == "PASS")
chk("JJ-VIEWS-03: 3,000,000 → PASS (inclusive)",
    _run_filter(tgt, md, _mk_video(views=3_000_000)) == "PASS")
chk("JJ-VIEWS-04: 3,000,001 → MAX_VIEWS_CUT",
    _run_filter(tgt, md, _mk_video(views=3_000_001)) == "MAX_VIEWS_CUT")

chk("JJ-DUR-01: 19 sec → FAIL (min 20)",
    _run_filter(tgt, md, _mk_video(duration=19)) != "PASS")
chk("JJ-DUR-02: exact 20 sec → PASS",
    _run_filter(tgt, md, _mk_video(duration=20)) == "PASS")
# 180초 max 는 없어야 함
chk("JJ-DUR-03: 181 sec → PASS (max_duration 없음, 실수로 180 max 적용되지 않음)",
    _run_filter(tgt, md, _mk_video(duration=181)) == "PASS")
chk("JJ-DUR-04: 600 sec (10분) → PASS (상한 없음)",
    _run_filter(tgt, md, _mk_video(duration=600)) == "PASS")

chk("JJ-AGE-01: exact 7d → PASS",
    _run_filter(tgt, md, _mk_video(days_ago=7)) == "PASS")
chk("JJ-AGE-02: 7d + 1s → FAIL",
    _run_filter(tgt, md, _mk_video(days_ago=7, seconds_extra=1)) != "PASS")


# ═══════════════════════════════════════════════════════════════════
# 2) 묘한덕질 boundary (동일 조건)
# ═══════════════════════════════════════════════════════════════════
tgt = "myohanduk"
chk("MH-VIEWS-01: 99,999 → FAIL",
    _run_filter(tgt, md, _mk_video(views=99_999)) != "PASS")
chk("MH-VIEWS-02: 100,000 → PASS",
    _run_filter(tgt, md, _mk_video(views=100_000)) == "PASS")
chk("MH-VIEWS-03: 3,000,000 → PASS",
    _run_filter(tgt, md, _mk_video(views=3_000_000)) == "PASS")
chk("MH-VIEWS-04: 3,000,001 → MAX_VIEWS_CUT",
    _run_filter(tgt, md, _mk_video(views=3_000_001)) == "MAX_VIEWS_CUT")

chk("MH-DUR-01: 19 sec → FAIL",
    _run_filter(tgt, md, _mk_video(duration=19)) != "PASS")
chk("MH-DUR-02: 20 sec → PASS",
    _run_filter(tgt, md, _mk_video(duration=20)) == "PASS")
chk("MH-DUR-03: 181 sec → PASS (max_duration 없음)",
    _run_filter(tgt, md, _mk_video(duration=181)) == "PASS")

chk("MH-AGE-01: exact 7d → PASS",
    _run_filter(tgt, md, _mk_video(days_ago=7)) == "PASS")
chk("MH-AGE-02: 7d + 1s → FAIL",
    _run_filter(tgt, md, _mk_video(days_ago=7, seconds_extra=1)) != "PASS")


# ═══════════════════════════════════════════════════════════════════
# 3) target 별 profile 정합성
# ═══════════════════════════════════════════════════════════════════
jj = get_target("jjalduk")
mh = get_target("myohanduk")

chk("PROF-JJ-01: jjalduk reference_channels 3개",
    len(jj["reference_channels"]) == 3)
chk("PROF-JJ-02: jjalduk MAX_DURATION_SEC = None",
    jj["modes"]["weekly"]["MAX_DURATION_SEC"] is None)
chk("PROF-JJ-03: jjalduk MAX_ANALYSIS = 30, FINAL = 5 (모든 target 30/5 통일)",
    jj["modes"]["weekly"]["MAX_ANALYSIS_CANDIDATES"] == 30
    and jj["modes"]["weekly"]["FINAL_CANDIDATES"] == 5)
chk("PROF-JJ-04: jjalduk legacy 관련 플래그 모두 False",
    jj.get("legacy_sent_history_flat") is False
    and jj.get("legacy_weekly_history") is False)
chk("PROF-JJ-05: jjalduk soft_guidance 존재 (positive+negative)",
    isinstance(jj["soft_guidance"]["positive_signals"], list)
    and isinstance(jj["soft_guidance"]["negative_signals"], list)
    and len(jj["soft_guidance"]["positive_signals"]) > 0
    and len(jj["soft_guidance"]["negative_signals"]) > 0)
chk("PROF-JJ-06: jjalduk soft_guidance 에 '엔믹스' 신호 포함",
    any("엔믹스" in s for s in jj["soft_guidance"]["positive_signals"]))
chk("PROF-JJ-07: jjalduk 정보성 부정 신호 = Hard rule 아님 명시",
    any("금지 아님" in s or "정보성" in s
        for s in jj["soft_guidance"]["negative_signals"]))
chk("PROF-JJ-08: jjalduk angle_field_label = 짤덕방식 활용 각도",
    jj["identity"]["angle_field_label"] == "짤덕방식 활용 각도")

chk("PROF-MH-01: myohanduk reference_channels 6개",
    len(mh["reference_channels"]) == 6)
chk("PROF-MH-02: myohanduk MAX_DURATION_SEC = None",
    mh["modes"]["weekly"]["MAX_DURATION_SEC"] is None)
chk("PROF-MH-03: myohanduk MAX_ANALYSIS = 30, FINAL = 5 (모든 target 30/5 통일)",
    mh["modes"]["weekly"]["MAX_ANALYSIS_CANDIDATES"] == 30
    and mh["modes"]["weekly"]["FINAL_CANDIDATES"] == 5)
chk("PROF-MH-04: myohanduk legacy 플래그 모두 False",
    mh.get("legacy_sent_history_flat") is False
    and mh.get("legacy_weekly_history") is False)
chk("PROF-MH-05: myohanduk 익명/실명 판단 가이드가 system prompt 에 존재",
    "익명" in mh["identity"]["analyze_video_system_prompt"]
    and "실명" in mh["identity"]["analyze_video_system_prompt"])
chk("PROF-MH-06: myohanduk '절대 규칙 채택하지 않음' 명시",
    "절대 규칙" in mh["identity"]["analyze_video_system_prompt"])
chk("PROF-MH-07: myohanduk angle_field_label = 묘한덕질식 활용 각도",
    mh["identity"]["angle_field_label"] == "묘한덕질식 활용 각도")


# ═══════════════════════════════════════════════════════════════════
# 4) sent history namespace 격리 — legacy read compat 흐름
# ═══════════════════════════════════════════════════════════════════
ns_jj, flat_jj, wk_jj, merged_jj = benchmark.load_sent_video_ids_for_target(jj)
ns_mh, flat_mh, wk_mh, merged_mh = benchmark.load_sent_video_ids_for_target(mh)
chk("HIS-NS-01: 짤덕방 legacy_flat empty (legacy 미참조)",
    len(flat_jj) == 0)
chk("HIS-NS-02: 짤덕방 legacy_weekly empty (legacy 미참조)",
    len(wk_jj) == 0)
chk("HIS-NS-03: 묘한덕질 legacy_flat empty",
    len(flat_mh) == 0)
chk("HIS-NS-04: 묘한덕질 legacy_weekly empty",
    len(wk_mh) == 0)
chk("HIS-NS-05: 짤덕방 merged = ns_jj (legacy 미참조 확인)",
    merged_jj == ns_jj)
chk("HIS-NS-06: 묘한덕질 merged = ns_mh (legacy 미참조 확인)",
    merged_mh == ns_mh)


# ═══════════════════════════════════════════════════════════════════
# 5) target 간 dedup 격리 — 상호 참조하지 않음
# ═══════════════════════════════════════════════════════════════════
# 실제 fs 파일 없이 loader 반환 값을 대상으로 상호 배타 확인.
# 만약 legacy 플래그가 True 인 target 있으면 flat/weekly 가 다른 target 에도 뿌려질 위험.
# 짤덕방/묘한덕질 은 legacy 플래그 False 이므로 서로 절대 참조하지 않음을 명시적으로 검증.
te = get_target("teoldeurim")
ns_te, flat_te, wk_te, merged_te = benchmark.load_sent_video_ids_for_target(te)
# te 의 flat/weekly 는 실제 파일이 있을 수 있으나, 짤덕방/묘한덕질 은 empty 여야 함.
chk("ISOL-01: 짤덕방 ns 는 털어드림 namespace 참조 안 함",
    ns_jj.isdisjoint(ns_te) or len(ns_jj) == 0)
chk("ISOL-02: 묘한덕질 ns 는 털어드림 namespace 참조 안 함",
    ns_mh.isdisjoint(ns_te) or len(ns_mh) == 0)


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 짤덕방 / 묘한덕질 boundary + dedup 격리 회귀 결과")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
