# -*- coding: utf-8 -*-
"""회귀 — recent 프로파일 신규 조건 검증 (2026-07-21 월·수·금 확장).

지키는 원칙:
  · Weekly Standard 판정 결과는 **완전히 이전과 동일** — 특히 정확히 9,000,000회는 컷.
  · Weekly Recent: MIN_VIEWS 50_000 / MAX_VIEWS 3_000_000 (inclusive, "이하"). 2026-08-06 상향.
  · UPLOADED_WITHIN_DAYS=7 (정확히 7일 pass, 7일 + 1초 cut).
  · MIN_AGE_DAYS_EXCLUSIVE=None (업로드 당일 = age 0일 포함).
  · MIN/MAX duration 15/180 유지.
  · Benchmark Standard 판정 결과 완전히 동일 (설정값·경계 로직 무변경).
  · load_seen_video_meta 가 standard + recent (월·수·금 각 실행일 파일) 전부 로드.

핵심 개선:
  · hard_filter_reason(row, now=None) 로 fixed_now 지원 → 정확한 timestamp 경계 검증.
  · WEEKLY_PROFILES 의 MAX_VIEWS_INCLUSIVE 로 상한 판정 스위칭 (standard=False / recent=True).

실행:
  프로젝트 루트에서:  python tests/test_recent_profile_50k_7d.py
  종료 코드: 성공 0 / 실패 1
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

for m in ["weekly", "benchmark", "benchmark_config"]:
    if m in sys.modules: del sys.modules[m]
import weekly, benchmark, benchmark_config

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


# ═══════════════════════════════════════════════════════════════════
# 1. WEEKLY_PROFILES / BENCHMARK_PROFILES 신규 값
# ═══════════════════════════════════════════════════════════════════
print("=" * 78)
print(" 1. Profile 값")
print("=" * 78)

# weekly recent
weekly._apply_weekly_profile("recent")
chk("W-P-01: weekly recent MIN_VIEWS=50_000",
    weekly.CONFIG["MIN_VIEWS"] == 50_000)
chk("W-P-02: weekly recent MAX_VIEWS=3_000_000 (2026-08-06 상향)",
    weekly.CONFIG["MAX_VIEWS"] == 3_000_000)
chk("W-P-03: weekly recent UPLOADED_WITHIN_DAYS=7",
    weekly.CONFIG["UPLOADED_WITHIN_DAYS"] == 7)
chk("W-P-04: weekly recent MIN_AGE_DAYS_EXCLUSIVE=None",
    weekly.CONFIG["MIN_AGE_DAYS_EXCLUSIVE"] is None)
chk("W-P-05: weekly recent MAX_VIEWS_INCLUSIVE=True",
    weekly.CONFIG.get("MAX_VIEWS_INCLUSIVE") is True)
chk("W-P-06: weekly recent MIN/MAX_DURATION 15/180",
    weekly.CONFIG.get("MIN_DURATION_SEC") == 15 and weekly.CONFIG["MAX_DURATION_SEC"] == 180)

# weekly standard — 값 그대로 유지 + 상한 판정 EXCLUSIVE
weekly._apply_weekly_profile("standard")
chk("W-S-01: weekly standard MIN_VIEWS=500_000 (기존 그대로)",
    weekly.CONFIG["MIN_VIEWS"] == 500_000)
chk("W-S-02: weekly standard MAX_VIEWS=9_000_000",
    weekly.CONFIG["MAX_VIEWS"] == 9_000_000)
chk("W-S-03: weekly standard UPLOADED_WITHIN_DAYS=365",
    weekly.CONFIG["UPLOADED_WITHIN_DAYS"] == 365)
chk("W-S-04: weekly standard MIN_AGE_DAYS_EXCLUSIVE=30",
    weekly.CONFIG["MIN_AGE_DAYS_EXCLUSIVE"] == 30)
chk("W-S-05: weekly standard MAX_VIEWS_INCLUSIVE=False (기존 exclusive 유지)",
    weekly.CONFIG.get("MAX_VIEWS_INCLUSIVE") is False)

# benchmark recent
bm_r = benchmark_config.resolve_config("recent")
chk("B-P-01: benchmark recent MIN_VIEWS=50_000", bm_r["MIN_VIEWS"] == 50_000)
chk("B-P-02: benchmark recent MAX_VIEWS=3_000_000 (2026-08-06 상향)", bm_r["MAX_VIEWS"] == 3_000_000)
chk("B-P-03: benchmark recent UPLOADED_WITHIN_DAYS=7", bm_r["UPLOADED_WITHIN_DAYS"] == 7)
chk("B-P-04: benchmark recent MIN_AGE_DAYS_EXCLUSIVE=None",
    bm_r["MIN_AGE_DAYS_EXCLUSIVE"] is None)

# benchmark standard — 기존 유지
bm_s = benchmark_config.resolve_config("standard")
chk("B-S-01: benchmark standard MIN_VIEWS=500_000", bm_s["MIN_VIEWS"] == 500_000)
chk("B-S-02: benchmark standard MAX_VIEWS=9_000_000", bm_s["MAX_VIEWS"] == 9_000_000)
chk("B-S-03: benchmark standard UPLOADED_WITHIN_DAYS=365", bm_s["UPLOADED_WITHIN_DAYS"] == 365)
chk("B-S-04: benchmark standard MIN_AGE_DAYS_EXCLUSIVE=30", bm_s["MIN_AGE_DAYS_EXCLUSIVE"] == 30)


# ═══════════════════════════════════════════════════════════════════
# 2. Weekly Standard — 조회수 경계 판정 **불변** 회귀 (가장 중요)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 2. Weekly Standard 조회수 경계 (기존 동작 완전 보존)")
print("=" * 78)
weekly._apply_weekly_profile("standard")


def wk_row(views, dur=60, age_days=100, pub_iso=None):
    """age_days=100 은 standard 통과 범위. published_at 을 명시하면 그 값 사용."""
    if pub_iso is None:
        pub = datetime.now(timezone.utc) - timedelta(days=age_days)
        pub_iso = pub.isoformat().replace("+00:00", "Z")
    return {
        "video_id": "X", "view_count": views, "duration_seconds": dur,
        "title": "테스트 영상 한글", "channel": "일반채널", "channel_id": "",
        "published_at": pub_iso,
    }


# ★ 사용자 명시 회귀: standard 는 정확히 9M 컷을 유지해야 함
STD_VIEW = [
    (499_999,   False, "499,999 → cut (미달)"),
    (500_000,   True,  "500,000 → pass"),
    (8_999_999, True,  "8,999,999 → pass"),
    (9_000_000, False, "9,000,000 → cut (기존 exclusive, 유지)"),
    (9_000_001, False, "9,000,001 → cut"),
]
for views, expect_pass, label in STD_VIEW:
    r = weekly.hard_filter_reason(wk_row(views))
    passed = (r == (None, None))
    chk(f"W-STD-{views:>10,}: {label}", passed == expect_pass,
        f"reason={r[0]!r}")


# ═══════════════════════════════════════════════════════════════════
# 3. Weekly Standard — age 경계 **불변** 회귀 (30일 초과 ~ 365일 이내)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 3. Weekly Standard age 경계 (30일 초과 ~ 365일 이내, 기존 유지)")
print("=" * 78)
weekly._apply_weekly_profile("standard")

fixed_now = datetime(2026, 7, 21, 0, 0, 0, tzinfo=timezone.utc)


def row_at_age(views, age_delta, dur=60):
    """fixed_now - age_delta 를 published_at 으로 사용."""
    pub = fixed_now - age_delta
    return {
        "video_id": "Y", "view_count": views, "duration_seconds": dur,
        "title": "한글 제목 테스트", "channel": "일반채널", "channel_id": "",
        "published_at": pub.isoformat().replace("+00:00", "Z"),
    }


# standard: MIN_AGE_DAYS_EXCLUSIVE=30 (exclusive) / UPLOADED_WITHIN_DAYS=365 (inclusive)
#   0 ~ 30일: cut (recent 소속) / 30일 + 1초: pass / 365일 정확: pass / 365일 + 1초: cut
std_age = [
    (timedelta(days=0),                    False, "0일 → cut (30일 이내)"),
    (timedelta(days=30),                   False, "정확히 30일 → cut (recent 소속)"),
    (timedelta(days=30, seconds=1),        True,  "30일 + 1초 → pass"),
    (timedelta(days=100),                  True,  "100일 → pass"),
    (timedelta(days=365),                  True,  "정확히 365일 → pass (inclusive)"),
    (timedelta(days=365, seconds=1),       False, "365일 + 1초 → cut"),
]
for delta, expect_pass, label in std_age:
    r = weekly.hard_filter_reason(row_at_age(1_000_000, delta), now=fixed_now)
    passed = (r == (None, None))
    chk(f"W-STD-AGE[{label}]", passed == expect_pass, f"reason={r[0]!r}")


# ═══════════════════════════════════════════════════════════════════
# 4. Weekly Recent — 조회수 경계 (5만 이상 ~ 300만 이하)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 4. Weekly Recent 조회수 경계")
print("=" * 78)
weekly._apply_weekly_profile("recent")

REC_VIEW = [
    (49_999,    False, "49,999 → cut"),
    (50_000,    True,  "50,000 → pass (하한 경계 inclusive)"),
    (50_001,    True,  "50,001 → pass"),
    (500_000,   True,  "500,000 → pass"),
    (1_000_000, True,  "1,000,000 → pass (이전 상한, 지금은 여유)"),
    (2_999_999, True,  "2,999,999 → pass"),
    (3_000_000, True,  "3,000,000 → pass (상한 경계 inclusive, 2026-08-06)"),
    (3_000_001, False, "3,000,001 → cut"),
]
for views, expect_pass, label in REC_VIEW:
    # recent 통과 범위 age (3일)
    r = weekly.hard_filter_reason(wk_row(views, age_days=3))
    passed = (r == (None, None))
    chk(f"W-REC-{views:>10,}: {label}", passed == expect_pass,
        f"reason={r[0]!r}")


# ═══════════════════════════════════════════════════════════════════
# 5. Weekly Recent — age 경계 (0 ~ 7일, fixed_now)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 5. Weekly Recent age 경계 (fixed_now)")
print("=" * 78)
weekly._apply_weekly_profile("recent")

rec_age = [
    (timedelta(0),                        True,  "정확히 fixed_now (age=0) → pass"),
    (timedelta(days=3),                   True,  "3일 → pass"),
    (timedelta(days=7),                   True,  "정확히 7일 → pass (inclusive)"),
    (timedelta(days=7, seconds=1),        False, "7일 + 1초 → cut"),
    (timedelta(days=7, seconds=60),       False, "7일 + 1분 → cut"),
    (timedelta(days=10),                  False, "10일 → cut"),
]
for delta, expect_pass, label in rec_age:
    r = weekly.hard_filter_reason(row_at_age(500_000, delta), now=fixed_now)
    passed = (r == (None, None))
    chk(f"W-REC-AGE[{label}]", passed == expect_pass, f"reason={r[0]!r}")


# ═══════════════════════════════════════════════════════════════════
# 6. Duration 경계 유지 (recent + standard 공통 15/180)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 6. Duration 경계 유지 (15/180초)")
print("=" * 78)
for profile in ["standard", "recent"]:
    weekly._apply_weekly_profile(profile)
    for dur, expect_pass in [(14, False), (15, True), (60, True), (180, True), (181, False)]:
        if profile == "standard":
            r = weekly.hard_filter_reason(wk_row(1_000_000, dur=dur, age_days=100))
        else:
            r = weekly.hard_filter_reason(wk_row(500_000, dur=dur, age_days=3))
        passed = (r == (None, None))
        chk(f"W-DUR-{profile}-{dur:>3}s", passed == (True if expect_pass else False),
            f"reason={r[0]!r}")


# ═══════════════════════════════════════════════════════════════════
# 7. Benchmark Recent 경계 (50k~1M / 0~7일 / 15~180초)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 7. Benchmark Recent 경계 (apply_max_views_filter + hard_filter 순차)")
print("=" * 78)
benchmark.CFG.clear()
benchmark.CFG.update(benchmark_config.resolve_config("recent"))


def bm_row(views, dur=60, age_days=3, pub_dt=None):
    if pub_dt is None:
        pub_dt = fixed_now - timedelta(days=age_days)
    return {
        "video_id": "V", "views": views, "duration_sec": dur,
        "published_at": pub_dt.isoformat().replace("+00:00", ".000Z"),
    }


for views, expect_pass, label in REC_VIEW:
    under, over = benchmark.apply_max_views_filter([bm_row(views)])
    if over:
        passed = False
        reason = f"over_max ({over[0].get('exclusion_reason','')})"
    else:
        pl, ex = benchmark.hard_filter(under, fixed_now)
        passed = len(pl) == 1
        reason = "(pass)" if passed else (ex[0][1] if ex else "?")
    chk(f"B-REC-{views:>10,}: {label}", passed == expect_pass,
        f"reason={reason!r}")

# age (fixed_now)
for delta, expect_pass, label in rec_age:
    pub = fixed_now - delta
    pl, ex = benchmark.hard_filter([bm_row(500_000, pub_dt=pub)], fixed_now)
    passed = len(pl) == 1
    chk(f"B-REC-AGE[{label}]", passed == expect_pass,
        f"ex={ex[0][1] if ex else '(pass)'!r}")

# duration
for dur, expect_pass in [(14, False), (15, True), (180, True), (181, False)]:
    pl, ex = benchmark.hard_filter([bm_row(500_000, dur=dur)], fixed_now)
    chk(f"B-REC-DUR-{dur:>3}s", (len(pl) == 1) == expect_pass)


# ═══════════════════════════════════════════════════════════════════
# 8. Benchmark Standard 경계 **불변** 회귀
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 8. Benchmark Standard 경계 (기존 유지)")
print("=" * 78)
benchmark.CFG.clear()
benchmark.CFG.update(benchmark_config.resolve_config("standard"))

# standard age: 30일 이하 컷 (recent 소속) / 30일 + 1초 pass / 365일 pass / 365일 + 1초 cut
std_age_bm = [
    (timedelta(days=0),                False, "0일 → cut (recent 소속)"),
    (timedelta(days=30),               False, "정확히 30일 → cut"),
    (timedelta(days=30, seconds=1),    True,  "30일 + 1초 → pass"),
    (timedelta(days=365),              True,  "정확히 365일 → pass"),
    (timedelta(days=365, seconds=1),   False, "365일 + 1초 → cut"),
]
for delta, expect_pass, label in std_age_bm:
    pub = fixed_now - delta
    pl, ex = benchmark.hard_filter([bm_row(1_000_000, pub_dt=pub)], fixed_now)
    passed = len(pl) == 1
    chk(f"B-STD-AGE[{label}]", passed == expect_pass,
        f"ex={ex[0][1] if ex else '(pass)'!r}")

# standard views: apply_max_views_filter 로 9M 이하 → hard_filter
for views, expect_pass, label in [
    (499_999,   False, "499,999 cut"),
    (500_000,   True,  "500,000 pass"),
    (8_999_999, True,  "8,999,999 pass"),
    # benchmark 는 apply_max_views_filter 가 `> MAX_VIEWS` → 9M pass, 9M+1 cut.
    # 이건 이번 변경 이전부터 그랬음 (변경 없음). standard 사용자 시각에서는
    # weekly 와 판정이 다를 수 있지만 그건 이번 커밋의 새 이슈가 아님.
    (9_000_000, True,  "9,000,000 pass (benchmark 는 원래 inclusive)"),
    (9_000_001, False, "9,000,001 cut (apply_max_views_filter over_max)"),
]:
    under, over = benchmark.apply_max_views_filter([bm_row(views, age_days=100)])
    if over:
        passed = False
    else:
        pl, ex = benchmark.hard_filter(under, fixed_now)
        passed = len(pl) == 1
    chk(f"B-STD-{views:>10,}: {label}", passed == expect_pass)


# ═══════════════════════════════════════════════════════════════════
# 9. Cross-profile dedup — 실행일별 recent 파일 여러 개 로드
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 9. Cross-profile dedup")
print("=" * 78)

with tempfile.TemporaryDirectory() as tmp:
    orig_hd = weekly.HISTORY_DIR
    weekly.HISTORY_DIR = Path(tmp)

    def mk_history(name, video_ids):
        p = Path(tmp) / name
        p.write_text(json.dumps({
            "date_kst": name.replace(".json", "").replace("_recent", ""),
            "candidates": [{"video_id": v} for v in video_ids],
        }), encoding="utf-8")

    mk_history("2026-07-17.json",         ["S1", "S2", "S3"])
    mk_history("2026-07-20_recent.json",  ["R1", "R2"])
    mk_history("2026-07-22_recent.json",  ["R3"])
    mk_history("2026-07-24_recent.json",  ["R4", "R5", "R6"])
    mk_history("2026-07-27_recent.json",  ["R7"])

    seen = weekly.load_seen_video_ids()
    chk("D-01: standard + recent 여러 실행일 모두 dedup 로드",
        seen == {"S1","S2","S3","R1","R2","R3","R4","R5","R6","R7"})
    chk("D-02: 정확히 10개 (standard 3 + recent 7)", len(seen) == 10)

    weekly.HISTORY_DIR = orig_hd


# ═══════════════════════════════════════════════════════════════════
# 10. 사용자-facing 문구
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 10. 사용자-facing 문구")
print("=" * 78)
weekly._apply_weekly_profile("recent")
chk("STR-01: _display_age_range recent = '업로드 직후 ~ 7일 이내'",
    weekly._display_age_range() == "업로드 직후 ~ 7일 이내")
weekly._apply_weekly_profile("standard")
chk("STR-02: _display_age_range standard = '30일 초과 ~ 365일 이내' (불변)",
    weekly._display_age_range() == "30일 초과 ~ 365일 이내")

if "send_report" in sys.modules: del sys.modules["send_report"]
os.environ.pop("REPORT_DATE", None); os.environ.pop("NOTICE", None); os.environ.pop("REISSUE", None)
import send_report

body = send_report._monday_body_html("2026-07-22", 5, 3, True, True)
chk("STR-03: monday body 에 '7일 이내' + '월·수·금' 명시",
    "0일 ~ 7일 이내" in body and "월·수·금" in body)
chk("STR-04: monday body 에 '조회수 5만~300만' + '업로드 0~7일'",
    "5만~300만" in body and "0~7일" in body)
chk("STR-05: 이전 30일/10만/100만 흔적 없음",
    "30일 이내" not in body and "10만~100만" not in body and "5만~100만" not in body)


# ═══════════════════════════════════════════════════════════════════
# 결과
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
