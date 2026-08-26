# -*- coding: utf-8 -*-
"""공용 벤치마크 config — target 무관 항목만 담습니다.

target 별 REFERENCE_CHANNELS / identity / modes / prompts 는
`config/targets/{slug}.py` 로 이관되었습니다 (2026-08-25 리팩터링).

이 파일은 다음 3가지 카테고리로 축소되었습니다:
  1) 우리 채널 (5개) + blocklist — 어떤 target 이든 참조 채널로 넣으면 안 됨
  2) Apify actor 및 공통 옵션
  3) benchmark 공용 sent history 위치

backward compat:
  · `resolve_config(profile)` 함수는 남겨두지만 내부적으로 target=teoldeurim 을
    로드하도록 리다이렉트. profile="standard"/"recent" 만 인식.
  · `AUTO_REFERENCE_FROM_HISTORY` 등 자동 참고 채널 확장 로직은 사용자 지시로
    완전히 제거했습니다 (모든 target 은 profile 의 명시적 목록으로만 관리).
"""

BENCHMARK_CONFIG = {
    # ========================================================================
    # 제외 채널 — 어느 target 이 되었든 절대 참고 채널로 삼거나 후보에 포함되면 안 됨.
    # 우리 채널 5개 + blocklist.
    # ========================================================================
    "EXCLUDE_CHANNELS": {
        "SOONIGROUP [수니그룹]",
        "연예부 김버니",
        "묘한덕질",
        "털어드림",
        "밈박스",
        "짤덕방",
        "Sha Trio",   # 2026-08-07 대표님 요청 (@Shatrio77)
    },
    # 채널 ID 기반 (채널명 변경에 영향 안 받음 — 더 안전)
    "EXCLUDE_CHANNEL_IDS": {
        "UC7m5t1dfCRmr_YjInZYurXQ",  # 연예부 김버니
        "UCww8_tNoNouU_qk1JxZnNLQ",  # 묘한덕질
        "UCfrO3ZMC-rOThB-NSxfGjTQ",  # 털어드림
        "UCJ-WDvNyJYnt-9lIIX7uKGA",  # 밈박스
        "UCcerVbAluh-1ifuEH6ZMqsw",  # 짤덕방
        "UC0z9zaHN6pwUoyGVyO8P4AQ",  # Sha Trio (@Shatrio77) — 2026-08-07
    },

    # ========================================================================
    # Apify 액터 설정
    # ========================================================================
    # 채널 단위 discovery — 참고 채널의 인기/최신 Shorts 를 대량 조회.
    # 정렬: POPULAR(인기순) / NEWEST / OLDEST — actor 특성상 oldestPostDate 지정 시
    # NEWEST 로 강제 리셋된다. 업로드 기간 컷은 액터에 맡기지 않고 로컬에서 처리.
    #
    # (2026-08-26) discovery actor 자체가 title(원본) + translatedTitle(번역) 을 함께
    # 반환하는 것을 실측 확인 (1,124건 검증). 별도 detail actor(youtube-scraper) 2차
    # 호출은 제거됨. Weekly Bundle 3 target 각 discovery 1회씩 = 총 3 runs.
    "APIFY_DISCOVERY_ACTOR": "streamers/youtube-shorts-scraper",

    # 이미 발송된 영상(sent history)은 후보 리스트에서 제외.
    # profile 별 dedup 적용 시점:
    #   · Weekly (pre_analysis_dedup=True): Claude 분석 전 (분석 슬롯 신규로 채움)
    #   · Standard (pre_analysis_dedup=False): Claude 분석 후 (기존 동작 유지)
    "EXCLUDE_SENT_FROM_CANDIDATES": True,

    # ========================================================================
    # 공통 상수 — target 별 override 불필요
    # ========================================================================
    "ANALYSIS_MODEL_DEFAULT": "claude-opus-4-7",
}


# benchmark 공용 sent history 루트 — target 별 서브디렉으로 저장.
#   신규:  benchmark/history/sent/{target}/YYYY-MM-DD_{mode}.json
#   legacy(털어드림 backward-compat, 읽기만): benchmark/history/sent/YYYY-MM-DD_{recent,standard}.json
BENCHMARK_SENT_HISTORY_DIR = "benchmark/history/sent"


# ============================================================================
# Backward-compat: 기존 코드가 여전히 resolve_config("standard"/"recent") 형태로
# 참조할 경우 target=teoldeurim + mode 매핑으로 리다이렉트. 신규 코드는
# config.targets.resolve_mode(target_slug, mode) 를 직접 사용하세요.
# ============================================================================
_PROFILE_TO_MODE = {
    "standard": "standard",
    "recent":   "weekly",   # 기존 recent = 매주 발송 = weekly
}


def resolve_config(profile="standard"):
    """(legacy) 기존 profile 이름 → teoldeurim target·mode 로 자동 매핑.

    새 코드는 targets.resolve_mode("teoldeurim", "weekly"|"standard") 를 사용하세요.
    이 함수는 리팩터링 후 test 재실행·resend 등의 backward-compat 목적으로만 남아있습니다.
    """
    # 지연 import — targets 패키지에서 이 파일을 참조하는 순환 회피용
    import sys, os
    _cfg_dir = os.path.dirname(__file__)
    if _cfg_dir not in sys.path:
        sys.path.insert(0, _cfg_dir)
    from targets import resolve_mode as _resolve_mode
    if profile not in _PROFILE_TO_MODE:
        raise ValueError(
            f"Unknown PROFILE={profile!r} (legacy). "
            f"Use targets.resolve_mode(target, mode) instead. "
            f"Legacy available: {sorted(_PROFILE_TO_MODE.keys())}"
        )
    mode = _PROFILE_TO_MODE[profile]
    merged = _resolve_mode("teoldeurim", mode)
    # 공용 config 를 함께 병합해 backward-compat 유지 (EXCLUDE_*, EXCLUDE_SENT_*, 등)
    for k, v in BENCHMARK_CONFIG.items():
        merged.setdefault(k, v)
    # legacy _PROFILE 필드도 유지 (기존 코드가 참조)
    merged["_PROFILE"] = profile
    return merged
