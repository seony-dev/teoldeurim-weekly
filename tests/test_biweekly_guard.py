# -*- coding: utf-8 -*-
"""격주 Standard 판정 (scripts/biweekly.py) 회귀.

지키는 원칙 (.github/workflows/weekly.yml shell guard 와 동일):
  · KST ISO 주차 홀수 → 실행, 짝수 → skip
  · 수동 override (FORCE_BIWEEKLY_RUN) → 주차 무관 실행
  · timezone-naive datetime 거부
  · 연말/연초 ISO 경계 · UTC↔KST 날짜 경계 정확

외부 API/메일/파일 쓰기 없음.

실행:
  python tests/test_biweekly_guard.py
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import biweekly

KST = timezone(timedelta(hours=9))
UTC = timezone.utc

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


def kst(y, m, d, hh=8, mm=23):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


# ═══════════════════════════════════════════════════════════════════
# 1) naive datetime 거부
# ═══════════════════════════════════════════════════════════════════
for label, fn in [("kst_iso_week", lambda: biweekly.kst_iso_week(datetime(2026, 11, 6, 8, 23))),
                  ("should_run_standard", lambda: biweekly.should_run_standard(datetime(2026, 11, 6))),
                  ("should_run_standard+force", lambda: biweekly.should_run_standard(datetime(2026, 11, 6), force=True))]:
    raised = False
    try:
        fn()
    except ValueError:
        raised = True
    chk(f"NAIVE[{label}]: naive datetime → ValueError", raised)


# ═══════════════════════════════════════════════════════════════════
# 2) 실 production Standard 발송일 (전부 scheduled · 홀수 주) → 실행
# ═══════════════════════════════════════════════════════════════════
_PROD_STANDARD_DATES = [
    (2026, 7, 17, 29), (2026, 7, 31, 31), (2026, 8, 14, 33), (2026, 8, 28, 35),
    (2026, 9, 11, 37), (2026, 9, 25, 39), (2026, 10, 9, 41), (2026, 10, 23, 43),
    (2026, 11, 6, 45),
]
for y, m, d, wk in _PROD_STANDARD_DATES:
    chk(f"PROD-{y}-{m:02d}-{d:02d}: W{wk} · scheduled → run",
        biweekly.kst_iso_week(kst(y, m, d)) == wk and biweekly.should_run_standard(kst(y, m, d)))
# GitHub schedule 지연으로 실제 시작이 오전/정오까지 밀려도 같은 판정
chk("PROD-DELAY: 2026-11-06 12:29 KST (지연 실행) → run",
    biweekly.should_run_standard(kst(2026, 11, 6, 12, 29)))


# ═══════════════════════════════════════════════════════════════════
# 3) 짝수 주 scheduled → skip / override → run
# ═══════════════════════════════════════════════════════════════════
for y, m, d, wk in [(2026, 10, 16, 42), (2026, 10, 30, 44), (2026, 11, 13, 46)]:
    t = kst(y, m, d)
    chk(f"EVEN-{y}-{m:02d}-{d:02d}: W{wk} · scheduled → skip",
        biweekly.kst_iso_week(t) == wk and not biweekly.should_run_standard(t))
    chk(f"EVEN-{y}-{m:02d}-{d:02d}: W{wk} · manual override → run",
        biweekly.should_run_standard(t, force=True))
chk("ODD-OVERRIDE: 홀수 주 manual override → run",
    biweekly.should_run_standard(kst(2026, 11, 6), force=True))
# 2026-07-20 (월, W30) 실제 수동 dispatch 성공 사례 — override 로 재현
chk("DISPATCH-2026-07-20: W30 (짝수) 실 수동 실행 → override 로 run",
    biweekly.kst_iso_week(kst(2026, 7, 20, 10, 58)) == 30
    and not biweekly.should_run_standard(kst(2026, 7, 20, 10, 58))
    and biweekly.should_run_standard(kst(2026, 7, 20, 10, 58), force=True))


# ═══════════════════════════════════════════════════════════════════
# 4) 연말/연초 ISO 경계 (shell `date +%V` 와 동일 결과)
# ═══════════════════════════════════════════════════════════════════
_BOUNDARY = [
    # (date, ISO week, run?)
    ((2025, 12, 26), 52, False),
    ((2026, 1, 2), 1, True),     # ISO 2026-W01 (2025-12-29 시작)
    ((2026, 1, 9), 2, False),
    ((2026, 12, 25), 52, False),
    ((2027, 1, 1), 53, True),    # 2026 은 53주 연도 → 2027-01-01 은 2026-W53
    ((2027, 1, 8), 1, True),     # W53 → W01: 홀수 주 연속 (shell guard 와 동일한 기존 동작)
    ((2027, 1, 15), 2, False),
    ((2020, 12, 31), 53, True),  # 다른 53주 연도
    ((2021, 1, 1), 53, True),
    ((2021, 1, 4), 1, True),
]
for (y, m, d), wk, run in _BOUNDARY:
    t = kst(y, m, d)
    chk(f"YEAR-{y}-{m:02d}-{d:02d}: W{wk:02d} → {'run' if run else 'skip'}",
        biweekly.kst_iso_week(t) == wk and biweekly.should_run_standard(t) == run,
        f"week={biweekly.kst_iso_week(t)}")


# ═══════════════════════════════════════════════════════════════════
# 5) KST 기준 판정 (UTC 와 날짜/주차가 갈리는 시각)
# ═══════════════════════════════════════════════════════════════════
# 일요일 15:30 UTC = 월요일 00:30 KST — KST 는 다음 주
t = datetime(2026, 11, 8, 15, 30, tzinfo=UTC)
chk("TZ-01: 2026-11-08 15:30 UTC (KST 월 00:30) → KST W46 (UTC 기준이면 W45)",
    biweekly.kst_iso_week(t) == 46 and t.isocalendar().week == 45)
chk("TZ-02: 위 시각 scheduled → skip (짝수, KST 기준)", not biweekly.should_run_standard(t))
# 실제 cron 시각: 목 23:23 UTC = 금 08:23 KST
chk("TZ-03: 2026-11-05 23:23 UTC (cron) → KST 금 W45 → run",
    biweekly.should_run_standard(datetime(2026, 11, 5, 23, 23, tzinfo=UTC)))
# 연초 경계: 2026-01-04 (일) 16:00 UTC = 2026-01-05 (월) 01:00 KST
t = datetime(2026, 1, 4, 16, 0, tzinfo=UTC)
chk("TZ-04: 2026-01-04 16:00 UTC → KST W02 (UTC 기준 W01)",
    biweekly.kst_iso_week(t) == 2 and t.isocalendar().week == 1)
# 다른 aware tz 입력도 KST 로 변환
t = datetime(2026, 11, 8, 10, 30, tzinfo=timezone(timedelta(hours=-5)))  # = 11-09 00:30 KST
chk("TZ-05: UTC-5 입력 → KST 로 변환해 판정 (W46)", biweekly.kst_iso_week(t) == 46)


# ═══════════════════════════════════════════════════════════════════
# 6) 독립 알고리즘 대조 — ISO 8601 목요일 규칙으로 2020~2032 매일 비교
# ═══════════════════════════════════════════════════════════════════
def _iso_week_thursday_rule(d):
    thursday = d + timedelta(days=3 - d.weekday())
    return (thursday - date(thursday.year, 1, 1)).days // 7 + 1

mismatch = []
d = date(2020, 1, 1)
while d <= date(2032, 12, 31):
    for hh in (0, 8, 23):
        t = datetime(d.year, d.month, d.day, hh, 0, tzinfo=KST)
        if biweekly.kst_iso_week(t) != _iso_week_thursday_rule(d):
            mismatch.append((d, hh))
    d += timedelta(days=1)
chk("ALGO: 2020-01-01~2032-12-31 매일 × 3시각 — 목요일 규칙과 전부 일치",
    not mismatch, f"mismatch={mismatch[:5]}")


# ═══════════════════════════════════════════════════════════════════
# 7) FORCE_BIWEEKLY_RUN 파싱
# ═══════════════════════════════════════════════════════════════════
for v in ("1", "true", "TRUE", "yes", " Yes "):
    chk(f"FORCE-ON[{v!r}]", biweekly.force_requested({"FORCE_BIWEEKLY_RUN": v}))
for v in ("", "0", "false", "no", "2"):
    chk(f"FORCE-OFF[{v!r}]", not biweekly.force_requested({"FORCE_BIWEEKLY_RUN": v}))
chk("FORCE-OFF[미설정]", not biweekly.force_requested({}))


# ═══════════════════════════════════════════════════════════════════
# 8) GitHub shell guard 가 그대로 존재 (이번 단계에서 제거하지 않음)
# ═══════════════════════════════════════════════════════════════════
_yml = (ROOT / ".github" / "workflows" / "weekly.yml").read_text(encoding="utf-8")
chk("YML-01: weekly.yml shell guard 유지 (TZ=Asia/Seoul date +%V)",
    "week=$(TZ=Asia/Seoul date +%V)" in _yml)
chk("YML-02: weekly.yml 홀수 판정 유지 ($((10#$week % 2)) -eq 1)",
    "$((10#$week % 2)) -eq 1" in _yml)
chk("YML-03: weekly.yml workflow_dispatch 무조건 통과 유지",
    '"${{ github.event_name }}" = "workflow_dispatch"' in _yml)
# (2026-11-09 cutover) 정기 실행은 Cloud Scheduler 로 이전 — GitHub schedule 은 없어야 한다
chk("YML-04: weekly.yml schedule 트리거 없음 (정기 실행은 Cloud Scheduler)",
    "schedule:" not in _yml and "cron:" not in _yml)


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 격주 Standard 판정 (KST ISO 주차) 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
