# -*- coding: utf-8 -*-
"""묘한덕질 target profile.

BASELINE:
  · 최근 Shorts 30개 표본 기준 실명 사용 비중 약 53% (실명·익명 혼재 채널).
  · 범 K-pop 가십·이슈·팬덤 반응형 포지션. 관계/갈등/이유형 서사가 자연스러움.
  · BTS 소재 중앙값은 낮은 편(약 2.0만) — 소재/포맷 재검토 여지.
  · 벤치마크 표본상 익명 프레이밍은 현역 아이돌 가십에서 성과 관찰
    (예: '아이돌' 지칭). 반대로 레거시 팬덤에서는 익명화가 역효과 가능.

BENCHMARK 채널 (사용자 확정 6개):
  · 베몽몬          @baemongmon
  · 아이돌모음zip   @idolclip_zip
  · 퇴근후아이돌    UCJBEBE9prUYeaqHwHvx3fOQ (채널 ID)
  · 로로단          @로로단
  · 애명            @ygeverythingyg
  · 아이돌탐정      @IDOL탐정

Hard filter 조건 (사용자 지시):
  · 조회수 100,000 이상 ~ 3,000,000 이하 (inclusive 양단)
  · 길이 20초 이상 (상한 없음 — MAX_DURATION_SEC=None)
  · 업로드 직후 ~ 7일 이내

Soft guidance:
  · 익명 프레이밍은 소재/팬덤 맥락에 따라 효과가 갈림 —
    "현역 K-pop 가십 = 무조건 익명" 같은 절대 규칙 금지.
"""

_ANALYZE_SYSTEM_PROMPT = """당신은 K-pop Shorts 채널 '묘한덕질'의 벤치마크 큐레이터입니다.
타 채널의 인기 Shorts를 보고 묘한덕질에 후보로 쓸 만한지 평가하고 기획 포인트를 뽑습니다.

# 채널 정체성

'묘한덕질'은 범 K-pop 가십·이슈·팬덤 반응형 Shorts 채널입니다.
관계 · 갈등 · 이유형 서사가 자연스럽고, 실명/익명이 혼재된 톤을 유지합니다.
최근 표본상 실명 사용 비중이 약 53% 이며, 소재·팬덤 맥락에 따라 익명 프레이밍이
효과적인 경우와 그렇지 않은 경우가 뚜렷이 갈립니다.

- 소재: 아이돌 관계·갈등·비하인드·팬덤 인사이더 이슈·경계선 가십
- 톤: 이슈 관찰자 시점 · 이유·맥락 서사 · 팬 반응 구조
- 지칭: 실명·익명 혼재 (소재/팬덤 맥락에 따라 선택)

# 지칭 판단 가이드 (참고용 · 인과 아님)

- 현역 K-pop 가십 중 특정 인물 프라이버시·법적 리스크가 개입되면 익명 프레이밍이
  안전한 편입니다. 벤치마크 표본에서는 '아이돌' 등 익명 지칭으로 강한 성과가
  관찰된 케이스가 있습니다.
- 반대로 레거시 팬덤·오래된 이슈 재조명·팬이 이미 잘 아는 서사에서는 실명을 유지하는 편이
  자연스럽고, 익명화가 역효과일 수 있습니다.
- '현역 K-pop 가십 = 무조건 익명' 같은 절대 규칙은 채택하지 않습니다.
  실명/익명 판단은 소재의 리스크 프로필과 팬덤 맥락을 함께 봅니다.

# 좋은 후보 / 나쁜 후보 신호

좋은 직접 후보:
- 1군 아이돌 관계·갈등·팬덤 이슈에 대한 관찰자형 서사
- 팬/팬덤 시점, 관계·갈등 구도, '왜/이유형' 훅
- 팬덤 인사이더 문화·해시태그·자음 리액션(ㅋㅋ·ㄷㄷ) 이 살아있는 구조
- 반전형 훅 · 감정어 마무리

주의:
- 순수 뮤직비디오/무대 본방 그 자체는 fit 낮음
- 영어 제목·해외 유튜버 중심 fit 낮음
- 최근 자체 표본 기준 BTS 단일 소재만 얹은 형태는 성과가 약함 (Hard 컷 아님)

지시받은 출력 형식(JSON)을 정확히 따르세요. JSON 외 텍스트는 출력하지 마세요.

# 벤치마크 평가 원칙

'묘한덕질에 그대로 쓸 수 있는가' 와 '소재·훅·구조를 묘한덕질식 관찰자 · 관계 서사로
가공할 가치가 있는가' 를 구분해 판단합니다. 반복 가능한 훅 · 팬덤 반응 구조 · 관계
서사가 있으면 벤치마크 가치가 있는 것으로 평가합니다.
"""

_ANALYZE_USER_PROMPT_INTRO = (
    "다음 YouTube Shorts가 '묘한덕질' 채널에서 다룰 만한 후보인지 평가하세요."
)
_ANALYZE_USER_PROMPT_ANGLE = (
    "- target_angle: 묘한덕질식 관찰자 · 관계 · 이유형 서사로 어떻게 재구성할지 한 줄"
)
_ANALYZE_USER_PROMPT_ANCHOR_HIGH = (
    "60~79: 묘한덕질식 관찰자·관계·팬덤 반응 서사로 변형 가능한 훅·구조가 명확"
)
_PATTERN_USER_LEAD = (
    "아래는 '묘한덕질' 벤치마크 분석 대상으로 선별된 타 채널 인기 Shorts입니다."
)
_PATTERN_DIRECT_LABEL = (
    "묘한덕질 직접 활용도가 높은 영상(fit_score 상위)의 공통 구조."
)


TARGET = {
    "slug": "myohanduk",
    "display_name": "묘한덕질",

    "identity": {
        "channel_context": (
            "범 K-pop 가십·이슈·팬덤 반응형 Shorts 채널. 실명/익명 혼재. "
            "관계·갈등·이유형 서사가 자연스러움."
        ),
        "angle_field_label": "묘한덕질식 활용 각도",
        "analyze_video_system_prompt": _ANALYZE_SYSTEM_PROMPT,
        "analyze_video_user_prompt_intro": _ANALYZE_USER_PROMPT_INTRO,
        "analyze_video_user_prompt_angle": _ANALYZE_USER_PROMPT_ANGLE,
        "analyze_video_anchor_high": _ANALYZE_USER_PROMPT_ANCHOR_HIGH,
        "pattern_user_lead": _PATTERN_USER_LEAD,
        "pattern_direct_label": _PATTERN_DIRECT_LABEL,
    },

    "soft_guidance": {
        "positive_signals": [
            "팬/팬덤 시점 · 관계·갈등 구도 · '왜/이유형' 훅",
            "익명 프레이밍이 리스크 프로필상 안전한 현역 아이돌 가십",
            "팬덤 인사이더 문화 · 해시태그 · 자음 리액션(ㅋㅋ·ㄷㄷ)",
            "반전형 훅 · 감정어 마무리",
        ],
        "negative_signals": [
            "레거시 팬덤·오래된 이슈에서 실명을 빼고 익명화만 강제한 형태 (역효과 여지)",
            "영어 제목·해외 유튜버 중심",
            "뮤직비디오·무대 본방 그 자체 (fit 낮음)",
            "BTS 단일 소재만 얹은 형태 (최근 자체 표본 상 성과 약세 — 절대 금지 아님)",
        ],
    },

    "reference_channels": [
        {"name": "베몽몬",       "channel": "https://www.youtube.com/@baemongmon"},
        {"name": "아이돌모음zip", "channel": "https://www.youtube.com/@idolclip_zip"},
        {"name": "퇴근후아이돌",  "channel": "https://www.youtube.com/channel/UCJBEBE9prUYeaqHwHvx3fOQ"},
        {"name": "로로단",       "channel": "https://www.youtube.com/@로로단"},
        {"name": "애명",         "channel": "https://www.youtube.com/@ygeverythingyg"},
        {"name": "아이돌탐정",   "channel": "https://www.youtube.com/@IDOL탐정"},
        {"name": "K챱챱",  "channel": "https://www.youtube.com/@K챱챱"},
    ],

    "modes": {
        "weekly": {
            "SORT_BY": "NEWEST",
            "MAX_SHORTS_PER_CHANNEL": 50,
            "MIN_VIEWS": 100_000,
            "MAX_VIEWS": 3_000_000,
            "MAX_VIEWS_INCLUSIVE": True,
            "MIN_DURATION_SEC": 20,
            "MAX_DURATION_SEC": None,    # 상한 없음 (사용자 지시)
            "MIN_AGE_DAYS_EXCLUSIVE": None,
            "UPLOADED_WITHIN_DAYS": 7,
            # MAX_ANALYSIS 30 은 padding 없는 upper cap. Hard·sent dedup 이후 후보가
            # 30 미만이면 실제 수만 분석되므로 6채널·raw 300 규모여도 낭비 없음.
            # (2026-08-25 대표님 확정: 모든 target/mode 30 통일)
            "MAX_ANALYSIS_CANDIDATES": 30,
            # FINAL=5 는 direct_final ≤5 + benchmark_final ≤5 각각의 cap (총 ≤10).
            "FINAL_CANDIDATES": 5,
            "PRE_ANALYSIS_DEDUP": True,
            "ANALYSIS_MODEL": "claude-opus-4-7",
        },
    },

    "history_namespace": "myohanduk",
    "history_shared_across_modes": False,
    "legacy_sent_history_flat": False,
    "legacy_weekly_history": False,
}
