# -*- coding: utf-8 -*-
"""짤덕방 target profile.

BASELINE:
  · 최근 Shorts 30개 표본 기반. 엔믹스 소재 비중 높고 성과 유의미하게 높음
    (엔믹스 중앙값 ~9.95만 vs 비-엔믹스 ~3.05만).
  · 실명 사용 자연 (약 57%). 대괄호 태그는 표본상 미사용.
  · '~한 이유' 마무리 43%.

BENCHMARK 채널 (사용자 확정 3개):
  · 쮸뀨미연구실  @jjyukkyumilab
  · 엔믹스자리표  UCQ49bhczsxg6DaVost3HM1A (채널 ID)
  · 또오해원      @ohhaewon

Hard filter 조건 (사용자 지시):
  · 조회수 100,000 이상 ~ 3,000,000 이하 (inclusive 양단)
  · 길이 20초 이상 (상한 없음 — MAX_DURATION_SEC=None)
  · 업로드 직후 ~ 7일 이내 (inclusive 상한)

Soft guidance:
  · Hard rule 로 승격 금지. 표본상 관찰된 상관관계 신호.
  · 비-엔믹스/정보성 소재를 절대 금지하지 말고 '표본 상 뚜렷한 약세' 로만 취급.
"""

_ANALYZE_SYSTEM_PROMPT = """당신은 K-pop Shorts 채널 '짤덕방'의 벤치마크 큐레이터입니다.
타 채널의 인기 Shorts를 보고 짤덕방에 후보로 쓸 만한지 평가하고 기획 포인트를 뽑습니다.

# 채널 정체성

'짤덕방'은 K-pop 아이돌 짤·리액션·비하인드·팬덤 반응을 중심으로 편집한
가벼운 예능 톤의 K-pop Shorts 채널입니다. 최근 30개 표본 기준 엔믹스 소재 비중이
높고, 엔믹스 관련 콘텐츠에서 성과 중앙값이 비-엔믹스 대비 뚜렷하게 높습니다.

- 소재: 아이돌 짤·리액션·비하인드·의외 순간·팬덤 인사이더 문화
- 톤: 가벼운 예능 · 짤·리액션 · 반전형 문구가 자연스러움
- 지칭: 실명 사용이 자연스러운 포지션 (표본 실명 비중 약 57%)

# 좋은 후보 / 나쁜 후보 신호

좋은 직접 후보:
- 1군 아이돌 (특히 엔믹스·현역 아이돌 대형 그룹) 관련 짤·리액션·팬덤 반응
- 의외성/반전 요소가 있는 짧은 순간 (실물 vs 무대, 예상 밖 반응)
- 멤버 페르소나가 뚜렷한 반복 캐릭터 소재
- 팬덤 반응·자막·짤이 살아있는 편집 가능한 원본

주의:
- 순수 뮤직비디오/무대 본방 그 자체는 짤·리액션 관점 없이는 fit 낮음
- 정보성 설명 콘텐츠(예: 산업 분석/역사 서술)는 최근 표본상 성과가 약함
  (Hard 컷 아님 — 실험 여지 있음)
- 영어 제목·해외 K-pop 리액션·해외 유튜버 중심은 fit 낮음

지시받은 출력 형식(JSON)을 정확히 따르세요. JSON 외 텍스트는 출력하지 마세요.

# 벤치마크 평가 원칙

'짤덕방에 그대로 쓸 수 있는가' 와 '소재·훅·구조를 짤덕방식으로 편집·리액션·짤 톤으로
가공할 가치가 있는가' 를 구분해 판단합니다. 반복 가능한 짤 포맷·팬덤 반응 구조·
멤버 페르소나가 있으면 벤치마크 가치가 있는 것으로 평가합니다.
"""

_ANALYZE_USER_PROMPT_INTRO = (
    "다음 YouTube Shorts가 '짤덕방' 채널에서 다룰 만한 후보인지 평가하세요."
)
_ANALYZE_USER_PROMPT_ANGLE = (
    "- target_angle: 짤덕방식 짤·리액션·편집 각도로 어떻게 활용할지 한 줄"
)
_ANALYZE_USER_PROMPT_ANCHOR_HIGH = (
    "60~79: 짤덕방식으로 편집·리액션 가공 가능한 훅·페르소나·팬덤 반응 포인트가 명확"
)
_PATTERN_USER_LEAD = (
    "아래는 '짤덕방' 벤치마크 분석 대상으로 선별된 타 채널 인기 Shorts입니다."
)
_PATTERN_DIRECT_LABEL = (
    "짤덕방 직접 활용도가 높은 영상(fit_score 상위)의 공통 구조."
)


TARGET = {
    "slug": "jjalduk",
    "display_name": "짤덕방",

    "identity": {
        "channel_context": (
            "K-pop 아이돌 짤·리액션·비하인드·팬덤 반응 중심의 가벼운 예능 톤 Shorts 채널. "
            "최근 표본상 엔믹스 소재 비중이 높음."
        ),
        "angle_field_label": "짤덕방식 활용 각도",
        "analyze_video_system_prompt": _ANALYZE_SYSTEM_PROMPT,
        "analyze_video_user_prompt_intro": _ANALYZE_USER_PROMPT_INTRO,
        "analyze_video_user_prompt_angle": _ANALYZE_USER_PROMPT_ANGLE,
        "analyze_video_anchor_high": _ANALYZE_USER_PROMPT_ANCHOR_HIGH,
        "pattern_user_lead": _PATTERN_USER_LEAD,
        "pattern_direct_label": _PATTERN_DIRECT_LABEL,
    },

    # 소프트 가이던스 — Hard rule 로 승격 금지. 실험 가설/soft signal.
    "soft_guidance": {
        "positive_signals": [
            "엔믹스 관련 소재 (최근 표본상 성과 유의미하게 높음)",
            "의외성·반전 훅 (짧은 순간의 갭)",
            "실명 사용이 자연스러운 편집",
            "멤버 페르소나가 반복되는 캐릭터형 소재",
            "팬덤 인사이더 문화·리액션·자막 짤",
        ],
        "negative_signals": [
            "정보성 설명 콘텐츠 (최근 표본에서 뚜렷한 약세 — 절대 금지 아님, 시도 여지 있음)",
            "영어 제목·해외 리액션·해외 유튜버 중심",
            "뮤직비디오·무대 본방 그 자체 (짤 관점 없이는 활용도 낮음)",
        ],
    },

    "reference_channels": [
        {"name": "쮸뀨미연구실", "channel": "https://www.youtube.com/@jjyukkyumilab"},
        {"name": "엔믹스자리표", "channel": "https://www.youtube.com/channel/UCQ49bhczsxg6DaVost3HM1A"},
        {"name": "또오해원",     "channel": "https://www.youtube.com/@ohhaewon"},
    ],

    "modes": {
        "weekly": {
            "SORT_BY": "NEWEST",
            "MAX_SHORTS_PER_CHANNEL": 50,
            "MIN_VIEWS": 100_000,
            "MAX_VIEWS": 3_000_000,
            "MAX_VIEWS_INCLUSIVE": True,
            "MIN_DURATION_SEC": 20,      # 20초 이상
            "MAX_DURATION_SEC": None,    # 상한 없음 (사용자 지시)
            "MIN_AGE_DAYS_EXCLUSIVE": None,
            "UPLOADED_WITHIN_DAYS": 7,
            # MAX_ANALYSIS 30 은 padding 없는 upper cap. 짤덕방은 3채널 × 50 = 150 raw
            # 로 실제 Hard 통과가 30 미만이 일반적이므로 사실상 상한선 역할.
            # (2026-08-25 대표님 확정: 모든 target/mode 30 통일)
            "MAX_ANALYSIS_CANDIDATES": 30,
            # FINAL=5 는 direct_final ≤5 + benchmark_final ≤5 각각의 cap (총 ≤10).
            "FINAL_CANDIDATES": 5,
            "PRE_ANALYSIS_DEDUP": True,
            "ANALYSIS_MODEL": "claude-opus-4-7",
        },
    },

    "history_namespace": "jjalduk",
    "history_shared_across_modes": False,   # jjalduk 은 weekly 만 존재
    "legacy_sent_history_flat": False,      # 신규 target — legacy 미참조
    "legacy_weekly_history": False,         # 신규 target — 털어드림 history 참조 금지
}
