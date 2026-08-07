# -*- coding: utf-8 -*-
"""
털어드림 타 채널 벤치마크 모듈 설정.

이 파일은 weekly.py와 완전히 독립적이다.
- weekly.py를 import 하지 않는다 (1차 구현 안전 우선).
- 제외 채널(우리 채널 + 기존 blocklist)은 여기에 직접 명시한다.
- 나중에 공통 config로 분리하는 건 2차 리팩토링으로 미룬다.
"""

BENCHMARK_CONFIG = {
    # ========================================================================
    # 벤치마크 대상 — 외부 참고 채널 (여기 등록된 채널만 수집)
    # ------------------------------------------------------------------------
    # name    : 사람이 알아보는 라벨 (리포트 표기용)
    # channel : Apify 액터에 넘길 값. 채널 username(@ 없이) 또는 채널 URL.
    #           예) "rainbowicecream9780"
    #               "https://www.youtube.com/@somechannel"
    #
    # ⚠️ 여기에 우리 채널이나 EXCLUDE 목록의 채널을 넣으면
    #    실행 시작 시 검증에서 즉시 중단된다.
    # ========================================================================
    "REFERENCE_CHANNELS": [
        # 형식: {"name": "라벨", "channel": "username 또는 채널 URL"}
        {"name": "패션탐정냥", "channel": "https://www.youtube.com/@tamjeongcat"},
        {"name": "덕칼럼", "channel": "https://www.youtube.com/channel/UC0l_Io9P2rTbM82PoC9Bi4w"},
        {"name": "이센느", "channel": "https://www.youtube.com/@이센느"},
        {"name": "리센느서치P", "channel": "https://www.youtube.com/@리센느서치P"},
        # 2026-08-05 대표님 요청 추가
        {"name": "베몽몬", "channel": "https://www.youtube.com/@baemongmon"},
        {"name": "팝픽", "channel": "https://www.youtube.com/@PopPickkk"},
        {"name": "로로단", "channel": "https://www.youtube.com/@로로단"},
        # 2026-08-06 대표님 요청 추가
        {"name": "방탄소년(진)", "channel": "https://www.youtube.com/@방탄소년진"},
    ],

    # ========================================================================
    # 자동 참고 채널 추출 — history/*.json (읽기 전용)에서 채널을 뽑아낸다.
    # ------------------------------------------------------------------------
    # weekly.py가 매주 골라낸 후보들의 채널 = "털어드림에 맞는 영상을 올리는 채널".
    # 그 채널들을 빈도순으로 집계해 상위 N개를 참고 채널로 자동 추가한다.
    # 최종 사용 = REFERENCE_CHANNELS(수동) + 자동 추출분 (중복 제거).
    #
    # ⚠️ history/*.json은 읽기만 한다 — 수정/삭제하지 않는다.
    #    weekly.py를 import 하지도 않는다.
    # ========================================================================
    "AUTO_REFERENCE_FROM_HISTORY": True,
    "AUTO_REFERENCE_TOP_N": 0,          # [임시] 이번 실행은 수동 채널만 — 검증 후 12로 복원
                                        # (=0이어도 sent_video_ids dedup은 유지됨)
    "AUTO_REFERENCE_MIN_SCORE": 2,      # 이 점수 미만 채널은 제외 (1회성 채널 컷)
    "HISTORY_CANDIDATE_WEIGHT": 3,      # 최종 채택(candidates) 등장 시 영상당 가중치
    "HISTORY_HARDPASS_WEIGHT": 1,       # Hard 통과(hard_passed) 등장 시 영상당 가중치

    # 이미 weekly에서 발송한 영상(history candidates)은 후보 리스트에서 제외.
    # (기획 포인트 분석에는 남겨둠 — "이게 먹혔다"는 참고 데이터로 유효)
    "EXCLUDE_SENT_FROM_CANDIDATES": True,

    # ========================================================================
    # 제외 채널 — 수집 / 분석 / 저장 / 리포트 어디에도 포함되면 안 되는 채널
    # (우리 채널 5개 + 기존 blocklist). weekly.py에서 import 하지 않고 직접 명시.
    # ========================================================================
    "EXCLUDE_CHANNELS": {
        "SOONIGROUP [수니그룹]",
        "연예부 김버니",
        "묘한덕질",
        "털어드림",
        "밈박스",
        "짤덕방",
    },
    # 채널 ID 기반 (채널명 변경에 영향 안 받음 — 더 안전)
    "EXCLUDE_CHANNEL_IDS": {
        "UC7m5t1dfCRmr_YjInZYurXQ",  # 연예부 김버니
        "UCww8_tNoNouU_qk1JxZnNLQ",  # 묘한덕질
        "UCfrO3ZMC-rOThB-NSxfGjTQ",  # 털어드림
        "UCJ-WDvNyJYnt-9lIIX7uKGA",  # 밈박스
        "UCcerVbAluh-1ifuEH6ZMqsw",  # 짤덕방
    },

    # ========================================================================
    # Apify 액터 설정
    # ========================================================================
    "APIFY_ACTOR": "streamers/youtube-shorts-scraper",
    # 정렬: POPULAR(인기순) / NEWEST / OLDEST
    # ⚠️ 액터 특성상 oldestPostDate를 넘기면 정렬이 NEWEST로 강제 리셋된다.
    #    그래서 업로드 기간 컷은 액터에 맡기지 않고 수집 후 로컬에서 처리한다.
    "SORT_BY": "POPULAR",
    "MAX_SHORTS_PER_CHANNEL": 50,   # 메가급 채널의 4M 이하 후보 확보 위해 상향
                                    # (이전 15도 패션탐정냥/덕칼럼은 상위 15개가 모두 400만 초과)
                                    # 8채널 × 50 = 400 items 최대 수집
    # (2026-08-07: MAX_TOTAL_RAW 상한 완전 제거. 초창기 2채널×15=30 기준의 안전값이었으나
    #  8채널로 확장하며 후보 다양성을 과도하게 제한하고 있었음. Claude 분석 비용 상한은
    #  MAX_ANALYSIS_CANDIDATES 로 별도 통제되므로 API 비용 영향 없음.)

    # ========================================================================
    # Hard 필터 — 수집 후 로컬에서 적용 (Claude 분석 전 1차 컷)
    # ========================================================================
    "MIN_VIEWS": 500_000,           # [테스트값] 운영 권장 100_000 (10만) → 50만으로 상향 (26.07.14)
    "MAX_VIEWS": 9_000_000,         # 조회수 상한 400만 → 900만으로 상향 (26.07.14)
                                    # (초대형 조회수 영상은 너무 뻔해서 후보 제외)
                                    # Hard 필터 이전에 별도 분리 (리포트 '조회수 초과 제외' 탭)
    "MAX_DURATION_SEC": 180,        # Shorts 형식 (상한)
    "MIN_DURATION_SEC": 15,         # 15초 미만 초단타 영상 제외 (26.07.15, 대표님 요청)
                                    # 최종 길이 조건: 15초 이상 ~ 180초 이하
    # 업로드 age 필터 — timestamp 기준 (초 단위 정확도). 정수 days_since_upload는 리포트 표시용.
    #   MIN_AGE_DAYS_EXCLUSIVE: age > N (strict, exclusive lower bound). None이면 하한 없음.
    #   UPLOADED_WITHIN_DAYS: age <= N (inclusive upper bound). None이면 상한 없음.
    # standard의 30일 초과~365일 이내를 정확히 표현. 정확히 30일 = recent 소속 (여기서 컷됨).
    "MIN_AGE_DAYS_EXCLUSIVE": 30,   # standard: 30일 이하 컷 (30 자체 포함해서 컷)
    "UPLOADED_WITHIN_DAYS": 365,    # 1년 이내 업로드만 (너무 오래된 영상 제외)

    # ========================================================================
    # Claude 분석 / 최종 후보
    # ========================================================================
    "ANALYSIS_MODEL": "claude-opus-4-7",
    "MAX_ANALYSIS_CANDIDATES": 10,  # [테스트값] 운영 권장 30 — Claude 분석 비용 상한
    "FINAL_CANDIDATES": 5,          # [테스트값] 운영 권장 15 — 리포트 노출 후보 수
}


# ============================================================================
# 프로파일 — 프로파일별로 override 되는 필드만 명시.
# 나머지 필드(REFERENCE_CHANNELS, EXCLUDE_*, ANALYSIS_MODEL 등)는
# BENCHMARK_CONFIG에서 상속.
# ----------------------------------------------------------------------------
# PROFILE 환경변수가 지정되지 않으면 "standard" 사용 (기존 격주 금요일 동작).
#
# ⚠️ standard 프로파일의 값은 반드시 위 BENCHMARK_CONFIG의 현재 값과 동일해야 함
#     (backward compat — 기존 실행 결과가 프로파일 도입으로 달라지면 안 됨).
# ============================================================================
BENCHMARK_PROFILES = {
    # 격주 금요일 — 30일 초과 ~ 365일 이내
    "standard": {
        "SORT_BY": "POPULAR",
        "MAX_SHORTS_PER_CHANNEL": 50,
        "MIN_VIEWS": 500_000,
        "MAX_VIEWS": 9_000_000,
        "MAX_DURATION_SEC": 180,
        "MIN_DURATION_SEC": 15,
        "MIN_AGE_DAYS_EXCLUSIVE": 30,     # 30일 이하는 recent 소속 → 여기서 컷
        "UPLOADED_WITHIN_DAYS": 365,      # 365일 초과 컷
        "MAX_ANALYSIS_CANDIDATES": 10,
        "FINAL_CANDIDATES": 5,
    },
    # 월·수·금 최근 콘텐츠 통합 리포트용 (0일 ~ 7일).
    #   2026-07-21 대표님 요청: 주 3회 발송 확장 + MIN_VIEWS 10만 → 5만
    #   / UPLOADED_WITHIN_DAYS 30일 → 7일 로 축소.
    "recent": {
        "SORT_BY": "NEWEST",
        "MAX_SHORTS_PER_CHANNEL": 50,
        "MIN_VIEWS": 50_000,
        "MAX_VIEWS": 3_000_000,
        "MAX_DURATION_SEC": 180,
        "MIN_DURATION_SEC": 15,
        # 업로드 age 범위: 0일 ≤ age ≤ 7일. 정확히 7일 timestamp도 여기 포함.
        "MIN_AGE_DAYS_EXCLUSIVE": None,   # 하한 없음 (업로드 당일 포함)
        "UPLOADED_WITHIN_DAYS": 7,        # 7일 초과 컷
        # 2026-07-14 실측 결과 (30일 기준) 반영: 채널당 조회수 분산이 커
        # top 10 만으로는 특정 채널 편중 → 15로 조정해 채널 다양성 확보.
        # (채널당 쿼터·라운드 로빈·점수 보정 없이 조회수 순 정렬 그대로 유지)
        "MAX_ANALYSIS_CANDIDATES": 15,
        "FINAL_CANDIDATES": 5,
    },
}


# benchmark 공용 sent history — recent/standard 모두 여기 기록·참조
BENCHMARK_SENT_HISTORY_DIR = "benchmark/history/sent"


def resolve_config(profile="standard"):
    """지정된 profile을 BENCHMARK_CONFIG에 병합해 반환. in-place 수정 안 함.

    사용:
        cfg = resolve_config(os.environ.get("PROFILE") or "standard")
        cfg["MIN_VIEWS"]   # 프로파일별 override 반영된 값
    """
    if profile not in BENCHMARK_PROFILES:
        raise ValueError(
            f"Unknown PROFILE={profile!r}. "
            f"Available: {sorted(BENCHMARK_PROFILES.keys())}"
        )
    merged = dict(BENCHMARK_CONFIG)
    merged.update(BENCHMARK_PROFILES[profile])
    merged["_PROFILE"] = profile
    return merged
