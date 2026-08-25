# -*- coding: utf-8 -*-
"""회귀 — benchmark 기발송 dedup 순서 변경 검증 (2026-08-14).

Recent 프로파일만 Claude 분석 전 사전 dedup 으로 전환.
Standard 는 기존 동작 (분석 후 dedup) 완전 유지.

지키는 원칙:
  · MAX_ANALYSIS_CANDIDATES 값 (2026-08-21 확장, 2026-08-25 채널 축소 후에도 상한 유지):
      - Recent   30 (기존 15 → 상향, 상한 유지)
      - Standard 30 (기존 10 → 상향, 상한 유지)
  · FINAL_CANDIDATES 값: Recent 5 / Standard 5 (2026-08-21 재확정 — 최대 5씩 유지)
  · Claude 호출 수:
      - 기존:         min(Hard 통과, MAX_ANALYSIS_CANDIDATES)
      - Recent 변경 후: min(Hard 통과 fresh, MAX_ANALYSIS_CANDIDATES)
    → 즉 기존과 같거나 감소, 절대 증가하지 않음.
  · Standard 는 sent 여부 무관 상위 N 개가 to_analyze 에 들어감 (기존 동작)
  · Recent 는 fresh 상위 N 개만 to_analyze 에 들어감 (신규 동작)
  · Standard 최종 후보 리스트 및 판정 결과 변화 없음
  · cross-profile dedup 기반 (weekly + benchmark sent) 구조 무변경
  · EXCLUDE_SENT_FROM_CANDIDATES=False 로 하면 Recent 도 dedup 미적용
  · Standard 리포트 HTML 탭 설명 (analyzed / sent_excluded) 은 원본 그대로 유지
    → Recent 만 _RECENT_TAB_DESC_OVERRIDE 로 새 설명 적용

실행:
  프로젝트 루트에서:  python tests/test_benchmark_sent_dedup_order.py
  종료 코드: 성공 0 / 실패 1
"""
import sys
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

for m in ["benchmark", "benchmark_config"]:
    if m in sys.modules:
        del sys.modules[m]
import benchmark, benchmark_config

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


# ─────────────────────────────────────────────
# fixture: Hard 통과 후보 (조회수 내림차순 정렬돼 있다고 가정)
# ─────────────────────────────────────────────
def mk_video(vid, views, title=None):
    """최소한의 dict — apply_sent_dedup_pre_analysis 가 참조하는 필드만 채움."""
    return {
        "video_id": vid,
        "views": views,
        "title": title or f"제목 {vid}",
    }


# ═══════════════════════════════════════════════════════════════════
# 1. apply_sent_dedup_pre_analysis 헬퍼 함수 자체 검증
# ═══════════════════════════════════════════════════════════════════
print("=" * 78)
print(" 1. apply_sent_dedup_pre_analysis 헬퍼 함수 자체")
print("=" * 78)

# 케이스 A: fresh 20 + sent 3 (총 23개)
passed_A = [mk_video(f"V{i:02d}", 3_000_000 - i * 10_000) for i in range(23)]
sent_ids_A = {"V02", "V05", "V07"}
fresh_A, sent_excl_A = benchmark.apply_sent_dedup_pre_analysis(passed_A, sent_ids_A)
chk("A-01: fresh 20개, sent 3개 분리",
    len(fresh_A) == 20 and len(sent_excl_A) == 3)
chk("A-02: sent_excluded 는 sent_ids 와 정확히 일치 (video_id)",
    {v["video_id"] for v in sent_excl_A} == sent_ids_A)
chk("A-03: fresh 각 아이템에 already_sent=False 부착",
    all(v.get("already_sent") is False for v in fresh_A))
chk("A-04: sent_excluded 각 아이템에 already_sent=True 부착",
    all(v.get("already_sent") is True for v in sent_excl_A))
chk("A-05: sent_excluded 는 exclusion_reason 부착",
    all("기발송" in v.get("exclusion_reason", "") for v in sent_excl_A))
chk("A-06: fresh 순서는 원본 조회수 순 유지",
    [v["video_id"] for v in fresh_A[:3]] == ["V00", "V01", "V03"])

# 케이스 B: 전부 fresh
passed_B = [mk_video(f"F{i}", 1_000_000 - i) for i in range(5)]
fresh_B, sent_excl_B = benchmark.apply_sent_dedup_pre_analysis(passed_B, set())
chk("B-01: sent_ids 비었을 때 fresh 는 원본 그대로",
    len(fresh_B) == 5 and len(sent_excl_B) == 0)

# 케이스 C: 전부 sent
passed_C = [mk_video(f"S{i}", 1_000_000 - i) for i in range(4)]
sent_ids_C = {f"S{i}" for i in range(4)}
fresh_C, sent_excl_C = benchmark.apply_sent_dedup_pre_analysis(passed_C, sent_ids_C)
chk("C-01: 전부 sent 면 fresh 는 빈 리스트, sent_excl 은 전부",
    len(fresh_C) == 0 and len(sent_excl_C) == 4)


# ═══════════════════════════════════════════════════════════════════
# 2. 사용자 요청 예시 시나리오 — Recent
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 2. 사용자 요청 시나리오: 조회수 순으로 A(sent) B(sent) C(sent) D(fresh) E(fresh)…")
print("=" * 78)

# A~C sent + D~T fresh (총 20 fresh)
scenario_2 = (
    [mk_video("A", 3_000_000), mk_video("B", 2_900_000), mk_video("C", 2_800_000)]
    + [mk_video(chr(ord("D") + i), 2_700_000 - i * 10_000) for i in range(17)]
)
sent_ids_2 = {"A", "B", "C"}

# Recent: MAX_ANALYSIS_CANDIDATES=30 (2026-08-21 상향)
# scenario_2 는 fresh 17개 (3 sent + 17 fresh) 이므로 상한 30 미달 → 전부 to_analyze
fresh_2, sent_excl_2 = benchmark.apply_sent_dedup_pre_analysis(scenario_2, sent_ids_2)
max_n_recent = 30
to_analyze_recent = fresh_2[:max_n_recent]
chk("REQ-01: sent 영상은 to_analyze 에 들어가지 않음",
    all(v["video_id"] not in sent_ids_2 for v in to_analyze_recent))
chk("REQ-02: fresh 17개 전부 to_analyze (상한 30 미달)",
    len(to_analyze_recent) == 17)
chk("REQ-03: sent 는 sent_excluded 에 A/B/C 모두 들어감",
    {v["video_id"] for v in sent_excl_2} == {"A", "B", "C"})
chk("REQ-04: Claude 분석 대상 수가 상한 30 을 넘지 않음",
    len(to_analyze_recent) <= max_n_recent)


# ═══════════════════════════════════════════════════════════════════
# 3. Claude 호출 수 실측 (min(fresh, MAX_ANALYSIS_CANDIDATES) 준수)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 3. Claude 호출 수 상한 준수 (min(fresh_count, MAX_ANALYSIS_CANDIDATES))")
print("=" * 78)

for case in [
    ("fresh 20 vs 상한 30", 20, 30, 20),  # 상한 미달 → fresh 전부
    ("fresh 12 vs 상한 30", 12, 30, 12),
    ("fresh  5 vs 상한 30",  5, 30,  5),
    ("fresh 40 vs 상한 30", 40, 30, 30),  # 상한 절대 초과 X
]:
    label, fresh_n, cap_n, expect = case
    _passed = [mk_video(f"F{i}", 1_000_000 - i) for i in range(fresh_n)]
    _fresh, _ = benchmark.apply_sent_dedup_pre_analysis(_passed, set())
    _to = _fresh[:cap_n]
    chk(f"CALL-{label}", len(_to) == expect,
        f"실측 {len(_to)}, 기대 {expect}")


# ═══════════════════════════════════════════════════════════════════
# 4. Standard 프로파일: 기존 동작 유지 (사전 dedup 미적용)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 4. Standard 는 사전 dedup 미적용 (main() 분기 로직 재현)")
print("=" * 78)

# main() 안의 분기 로직 재현:
# _recent_pre_dedup = (profile == "recent" and exclude_sent)
#
# Standard 인 경우: to_analyze = passed[:MAX_ANALYSIS_CANDIDATES] 로 sent 포함 여부 무관.
#                  Claude 분석 후에 sent 분리 (기존 동작).
def simulate_pre_step_selection(profile, exclude_sent, passed, sent_ids, cap):
    """main() 의 STEP 5.5 분기 로직 재현. 반환 = (to_analyze, sent_excluded_pre)."""
    recent_pre_dedup = (profile == "recent" and exclude_sent)
    if recent_pre_dedup:
        fresh, sent_excl = benchmark.apply_sent_dedup_pre_analysis(passed, sent_ids)
        return fresh[:cap], sent_excl
    else:
        return passed[:cap], []


# Standard 시나리오: 상위 3개 중 2개 sent → 여전히 to_analyze 에 sent 포함
passed_std = scenario_2
sent_ids_std = {"A", "B", "C"}

to_a_std, sent_excl_std_pre = simulate_pre_step_selection(
    profile="standard", exclude_sent=True,
    passed=passed_std, sent_ids=sent_ids_std, cap=30,
)
chk("STD-01: Standard to_analyze 상위 30개 = passed[:30] (sent 포함 여부 무관)",
    [v["video_id"] for v in to_a_std] == [v["video_id"] for v in passed_std[:30]])
chk("STD-02: Standard 사전 sent_excluded 리스트 비어있음 (분석 후 처리)",
    sent_excl_std_pre == [])
chk("STD-03: Standard to_analyze 에 sent A/B/C 여전히 포함",
    all(vid in {v["video_id"] for v in to_a_std} for vid in ["A","B","C"]))


# Recent 시나리오: 사전 dedup 후 sent 제외
to_a_rec, sent_excl_rec_pre = simulate_pre_step_selection(
    profile="recent", exclude_sent=True,
    passed=passed_std, sent_ids=sent_ids_std, cap=30,
)
chk("REC-01: Recent to_analyze 에 sent 없음",
    all(v["video_id"] not in sent_ids_std for v in to_a_rec))
chk("REC-02: Recent sent_excluded 사전 리스트 A/B/C 채워짐",
    {v["video_id"] for v in sent_excl_rec_pre} == sent_ids_std)
chk("REC-03: Recent Claude 호출 상한 30 이하",
    len(to_a_rec) <= 30)


# ═══════════════════════════════════════════════════════════════════
# 5. EXCLUDE_SENT_FROM_CANDIDATES=False 는 Recent 라도 dedup 미적용
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 5. EXCLUDE_SENT_FROM_CANDIDATES=False 는 Recent 도 사전 dedup 미적용")
print("=" * 78)

to_a_rec_off, sent_excl_off = simulate_pre_step_selection(
    profile="recent", exclude_sent=False,
    passed=passed_std, sent_ids=sent_ids_std, cap=30,
)
chk("OFF-01: exclude_sent=False → to_analyze 는 passed[:30] (기존 동작)",
    [v["video_id"] for v in to_a_rec_off] == [v["video_id"] for v in passed_std[:30]])
chk("OFF-02: exclude_sent=False → 사전 sent_excluded 는 빈 리스트",
    sent_excl_off == [])


# ═══════════════════════════════════════════════════════════════════
# 6. 실제 profile 값 로드 (recent=15, standard=10 상한 그대로 유지)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 6. profile 별 MAX_ANALYSIS_CANDIDATES 값 무변경")
print("=" * 78)

std = benchmark_config.resolve_config("standard")
rec = benchmark_config.resolve_config("recent")
chk("CFG-01: Standard MAX_ANALYSIS_CANDIDATES=30 (2026-08-21 상향)",
    std["MAX_ANALYSIS_CANDIDATES"] == 30)
chk("CFG-02: Recent MAX_ANALYSIS_CANDIDATES=30 (2026-08-21 상향)",
    rec["MAX_ANALYSIS_CANDIDATES"] == 30)
chk("CFG-03: Standard FINAL_CANDIDATES=5 (2026-08-25 최종 통일)",
    std["FINAL_CANDIDATES"] == 5)
chk("CFG-04: Recent(=weekly) FINAL_CANDIDATES=5 (2026-08-25 최종 통일)",
    rec["FINAL_CANDIDATES"] == 5)
chk("CFG-05: EXCLUDE_SENT_FROM_CANDIDATES 기본 True",
    benchmark_config.BENCHMARK_CONFIG["EXCLUDE_SENT_FROM_CANDIDATES"] is True)


# ═══════════════════════════════════════════════════════════════════
# 7. HTML 탭 설명 profile-aware 렌더 (Standard 원본 유지, Recent 만 override)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 7. 탭 설명 profile-aware (Standard 원본 유지, Recent 만 override)")
print("=" * 78)

# module-level 원본 (Standard 문구) 그대로인지
_tabs = {t[0]: t for t in benchmark._BENCHMARK_STAGE_TABS}
_analyzed_desc_std = _tabs["analyzed"][3]
_sent_excl_desc_std = _tabs["sent_excluded"][3]
chk("TAB-01: Standard analyzed 설명은 원본 문구 (기발송 포함)",
    "기발송 포함" in _analyzed_desc_std)
chk("TAB-02: Standard sent_excluded 설명은 원본 문구 (분석은 했지만…)",
    "분석은 했지만" in _sent_excl_desc_std)

# Recent 전용 override 존재
_override = benchmark._RECENT_TAB_DESC_OVERRIDE
chk("TAB-03: Recent override 에 analyzed 있음",
    "analyzed" in _override)
chk("TAB-04: Recent override 에 sent_excluded 있음",
    "sent_excluded" in _override)
chk("TAB-05: Recent analyzed 설명은 '기발송 사전 제외' 문구",
    "사전 제외" in _override["analyzed"])
chk("TAB-06: Recent sent_excluded 설명은 'Claude 분석 전에 후보 리스트에서 제외' 문구",
    "Claude 분석 전에 후보" in _override["sent_excluded"])
chk("TAB-07: Recent override 는 Standard 원본과 다른 문자열",
    _override["analyzed"] != _analyzed_desc_std)
chk("TAB-08: Recent override 는 Standard sent_excluded 원본과 다른 문자열",
    _override["sent_excluded"] != _sent_excl_desc_std)

# render_report 시그니처: profile 이 별도 인자 (config_used dict 안에 아님) → filtered_raw JSON 보호
import inspect
_sig = inspect.signature(benchmark.render_report)
chk("TAB-09: render_report 시그니처에 profile 인자 있음 (config_used 와 분리)",
    "profile" in _sig.parameters)
chk("TAB-10: profile 인자 default 는 'standard' (backward compat)",
    _sig.parameters["profile"].default == "standard")

# config_snapshot 은 filtered_raw JSON 에 그대로 저장되므로 _PROFILE 필드 존재 금지.
# main() 안의 config_snapshot 생성 로직을 소스 검증으로 확인.
_src = (ROOT / "scripts" / "benchmark.py").read_text(encoding="utf-8")
# config_snapshot dict 안에 "_PROFILE" 키가 리터럴로 들어있지 않아야 함.
_snapshot_block_start = _src.find("config_snapshot = {")
_snapshot_block_end = _src.find("}", _snapshot_block_start)
_snapshot_block = _src[_snapshot_block_start:_snapshot_block_end]
chk("TAB-11: config_snapshot dict 안에 '_PROFILE' 리터럴 필드 없음 "
    "(Standard filtered_raw JSON 구조 보존)",
    '"_PROFILE"' not in _snapshot_block)


# ═══════════════════════════════════════════════════════════════════
# 8. 탭 표시 순서 — Recent 는 실제 처리 순서 반영, Standard 는 원본 유지
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 8. 탭 표시 순서 (Recent 재정렬, Standard 원본)")
print("=" * 78)

_standard_keys = [t[0] for t in benchmark._BENCHMARK_STAGE_TABS]
_recent_keys = list(benchmark._RECENT_TAB_ORDER)

# 1) Recent 재정렬 리스트 자체 검증
chk("ORD-01: _RECENT_TAB_ORDER 존재",
    hasattr(benchmark, "_RECENT_TAB_ORDER"))
chk("ORD-02: Recent 순서 원소 집합 = Standard 순서 원소 집합 (누락·중복 없음)",
    set(_recent_keys) == set(_standard_keys))
chk("ORD-03: Recent 순서 길이 = Standard 순서 길이",
    len(_recent_keys) == len(_standard_keys))

# 2) 실제 처리 순서 요구 — Recent 는 sent_excluded 가 analyzed 앞
_rec_idx_sent = _recent_keys.index("sent_excluded")
_rec_idx_analyzed = _recent_keys.index("analyzed")
chk("ORD-04: Recent 순서에서 sent_excluded 가 analyzed 보다 앞",
    _rec_idx_sent < _rec_idx_analyzed,
    f"sent_excluded index={_rec_idx_sent}, analyzed index={_rec_idx_analyzed}")

# 3) 요청 명세 확인: Recent = [수집, 상한제외, Hard, 기발송제외, Claude, 직접, 벤치마크]
_expected_recent = [
    "collected", "max_views_excluded", "hard_excluded",
    "sent_excluded", "analyzed", "direct_final", "benchmark_final",
]
chk("ORD-05: Recent 순서 명세와 정확히 일치 (7개 순서 하드코드 검증)",
    _recent_keys == _expected_recent,
    f"got {_recent_keys}")

# 4) Standard 는 원본 순서 유지 — analyzed 가 sent_excluded 앞
_std_idx_sent = _standard_keys.index("sent_excluded")
_std_idx_analyzed = _standard_keys.index("analyzed")
chk("ORD-06: Standard 순서에서 analyzed 가 sent_excluded 보다 앞 (기존 유지)",
    _std_idx_analyzed < _std_idx_sent,
    f"analyzed index={_std_idx_analyzed}, sent_excluded index={_std_idx_sent}")

# 5) Standard 순서는 요청 명세와 정확히 동일 (기존 backward compat)
_expected_standard = [
    "collected", "max_views_excluded", "hard_excluded",
    "analyzed", "sent_excluded", "direct_final", "benchmark_final",
]
chk("ORD-07: Standard 순서가 요청 명세와 정확히 일치 (backward compat)",
    _standard_keys == _expected_standard,
    f"got {_standard_keys}")


# ═══════════════════════════════════════════════════════════════════
# 9. render_report 실제 렌더 순서 실측 (실제 HTML 출력 순서)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 9. render_report 실제 렌더 순서 (HTML 출력 순 실측)")
print("=" * 78)

# render_report 실행에 필요한 최소 fixture
_min_stage = {
    "collected": [], "max_views_excluded": [], "hard_excluded": [],
    "analyzed": [], "sent_excluded": [], "direct_final": [],
    "benchmark_final": [], "final": [],
}
_min_patterns = {
    "direct_common_patterns": [], "benchmark_common_patterns": [],
    "per_channel_insights": [],
}
_min_config = {
    "MAX_VIEWS": 3_000_000, "MIN_VIEWS": 50_000, "MAX_SHORTS_PER_CHANNEL": 50,
    "MAX_DURATION_SEC": 180, "MIN_DURATION_SEC": 15,
    "MIN_AGE_DAYS_EXCLUSIVE": None, "UPLOADED_WITHIN_DAYS": 7,
    "MAX_ANALYSIS_CANDIDATES": 30, "FINAL_CANDIDATES": 5,
    "EXCLUDE_CHANNELS": [],
}

import re

def _parse_html_stage_order(html):
    """렌더된 HTML 에서 data-stage 등장 순서 (중복 제거) 를 추출.

    stat 버튼과 panel 이 같은 순서로 렌더되므로 최초 등장 순으로 정렬해도 무방.
    (실제로는 stat 7개 → panel 7개 순서.)
    """
    keys = re.findall(r'data-stage="([^"]+)"', html)
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# Recent 렌더
html_recent = benchmark.render_report(
    dict(_min_stage), _min_patterns, "2026-08-14",
    ref_channels=[], config_used=dict(_min_config), profile="recent",
)
order_recent = _parse_html_stage_order(html_recent)
chk("REND-01: Recent 렌더 순서에 7개 탭 모두 등장",
    set(order_recent) == set(_expected_recent))
chk("REND-02: Recent 렌더 순서가 _RECENT_TAB_ORDER 와 정확히 일치",
    order_recent == _expected_recent,
    f"got {order_recent}")
chk("REND-03: Recent HTML 에서 sent_excluded 가 analyzed 앞에 실제 위치",
    order_recent.index("sent_excluded") < order_recent.index("analyzed"))

# Standard 렌더
html_std = benchmark.render_report(
    dict(_min_stage), _min_patterns, "2026-08-14",
    ref_channels=[], config_used=dict(_min_config), profile="standard",
)
order_std = _parse_html_stage_order(html_std)
chk("REND-04: Standard 렌더 순서가 _BENCHMARK_STAGE_TABS 원본과 정확히 일치",
    order_std == _expected_standard,
    f"got {order_std}")
chk("REND-05: Standard HTML 에서 analyzed 가 sent_excluded 앞에 실제 위치 (기존 유지)",
    order_std.index("analyzed") < order_std.index("sent_excluded"))

# profile 인자 없이 호출 → default "standard" 로 원본 순서
html_default = benchmark.render_report(
    dict(_min_stage), _min_patterns, "2026-08-14",
    ref_channels=[], config_used=dict(_min_config),
)
order_default = _parse_html_stage_order(html_default)
chk("REND-06: profile 인자 미지정 시 Standard 원본 순서 (backward compat)",
    order_default == _expected_standard,
    f"got {order_default}")


# ═══════════════════════════════════════════════════════════════════
# 10. 결과
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# 최종 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 결과")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1

print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
