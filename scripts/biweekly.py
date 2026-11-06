# -*- coding: utf-8 -*-
"""격주 Standard 실행 판정 (KST ISO 주차).

.github/workflows/weekly.yml 의 shell guard 와 동일한 규칙:
  · KST 기준 ISO 주차 (`TZ=Asia/Seoul date +%V`) 가 홀수 → 실행
  · 짝수 → skip
  · 수동 실행 override (FORCE_BIWEEKLY_RUN) → 주차 무관 실행

ISO 53주 연도에서는 W53 → 다음 해 W01 로 홀수 주가 연속된다 (shell guard 와 동일 동작).
표준 라이브러리만 사용.
"""

import os
from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9))


def kst_iso_week(now):
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("timezone-aware datetime 이 필요합니다 (naive datetime 금지)")
    return now.astimezone(KST).isocalendar().week


def force_requested(environ=None):
    env = os.environ if environ is None else environ
    return (env.get("FORCE_BIWEEKLY_RUN") or "").strip().lower() in ("1", "true", "yes")


def should_run_standard(now, force=False):
    week = kst_iso_week(now)
    return force or week % 2 == 1
