# -*- coding: utf-8 -*-
"""털어드림 target profile.

기존 scripts/benchmark.py 및 config/benchmark_config.py 에 하드코딩되어 있던
털어드림 identity / reference / modes 를 이곳으로 이관합니다.

리팩터링 원칙:
  · Claude 분석·최종 결과가 profile 이동 전과 동일하도록, prompt 문안·상한 값·
    hard filter 경계·정렬 규칙은 그대로 유지합니다.
  · MAX_VIEWS 는 이번 최신 요구사항대로 inclusive 로 통일 (3M/9M PASS).
  · MAX_ANALYSIS 30 / FINAL 5 통일 (2026-08-25 최종 재확정).
    - MAX_ANALYSIS=30 은 padding 없는 upper cap. Hard·sent dedup 이후 후보가 30 미만이면
      실제 후보 수만 Claude 분석.
    - FINAL=5 는 전체 최종 5개가 아니라 direct_final ≤5 + benchmark_final ≤5 각각의 cap.
      두 리스트는 상호 배타 (direct 든 video_id 는 benchmark 에서 제외 후 승격).
  · Weekly 모드 조건은 기존 "recent" 프로파일 값을 그대로 사용.
  · Standard 모드 조건은 기존 "standard" 프로파일 값을 유지하되 MAX_VIEWS inclusive.
"""

# ============================================================================
# 시스템 프롬프트 — Claude 분석의 정체성 / 판정 기준.
#   · 기존 BENCHMARK_SYSTEM_PROMPT 원문을 그대로 옮김.
#   · 이 텍스트가 바뀌면 Claude 판정 결과가 달라지므로 임의 수정 금지.
# ============================================================================
_ANALYZE_SYSTEM_PROMPT = """당신은 K-pop Shorts 채널 '털어드림'의 벤치마크 큐레이터입니다.
타 채널의 인기 Shorts를 보고, 털어드림에 후보로 쓸 만한지 평가하고 기획 포인트를 뽑습니다.

# 채널 정체성

'털어드림'은 K-pop 연예인 중심의 이슈·비하인드·관계성 분석형 Shorts 채널입니다.
단순 가십이나 장면 전달이 아니라, 인물의 행동·감정·관계·산업 구조 속에서
"왜 이런 장면이 나왔는지, 어떤 맥락이 있는지, 무엇이 반복 소비되는지"를 분석적으로 다룹니다.

핵심 공식 — 시의성 지연:
  1차(이슈 자체) → 2차(반응 정리·해석형) → 3차(여론 분석·산업 영향)
이슈 직후 단순 보도가 아닌, 2차/3차 해석으로 재가공해 시간이 지나도 유효한
구조적 분석을 추구합니다. 휘발성 콘텐츠 ❌, 반복 가능한 구조적 콘텐츠 ✅.

# 8가지 핵심 소재 유형

털어드림에 직접 사용할 후보는 아래 8가지 유형 중 하나에 명확히 속하는 것을 우선합니다.
다만 벤치마크 목적에서는 8가지 유형에 완전히 속하지 않더라도,
재사용 가능한 훅·제목 구조·관계성·페르소나·팬덤 반응 구조가 있으면 참고 가치가 있다고 판단할 수 있습니다.

1. K-pop 산업 분석: 계약·경제·정책·소속사 시스템
2. 아이돌 뒷이야기: 녹음실·촬영장·연습실 비하인드
3. 아이돌 일상 상황극: 실물 vs 무대, 기대 vs 현실 갭
4. 뮤직비디오 비하인드: 위험 촬영, NG 장면, 메이킹 디테일
5. 아이돌 고충 분석: 수면·다이어트·스케줄·건강·심리
6. **관계성·계보 분석**: 선배 → 후배 영향, 그룹 간 계보, 멤버 간 관계,
   팬-아이돌 상호작용에서 반복되는 관계 패턴
7. 실력/논란 해설: 안무·라이브·인성 논란의 구조적 이유
8. 연습생/데뷔 비하인드: 회사 결정·트레이닝 시스템·데뷔 과정

# 5가지 훅 패턴 (성공 영상의 첫 3초 구조)

1. 비교형: 과거 vs 현재 / 기대 vs 현실 / 선배 vs 후배
2. 의외성형: 충격적 사실, 위험한 상황, 예상 밖 폭로
3. 극한상황형: 수치·극단 강조 (72시간, 5년의 연습생, 죽기 살기)
4. 설명형: 기술·경제·구조적 배경을 분석적으로 해부
5. 유머형: 자조적 유머, 놀라운 장면, 위트 있는 표현

# 1군 아이돌 정의

대형/중대형 기획사 소속 + 대중 인지도 높은 현역 그룹의 멤버 또는 그룹 자체:
- HYBE: BTS, NewJeans/NJZ, TXT, ENHYPEN, ILLIT, LE SSERAFIM, SEVENTEEN, &TEAM
- SM: aespa, RIIZE, NCT(127/Dream/WayV), Red Velvet, Hearts2Hearts
- YG: BLACKPINK, BABYMONSTER, TREASURE
- JYP: TWICE, ITZY, NMIXX, Stray Kids
- 기타: IVE, (G)I-DLE, MAMAMOO, ATEEZ, TWS, KISS OF LIFE, BOYNEXTDOOR,
  fromis_9, ZEROBASEONE, RESCENE
- 솔로로 트렌드 1군 진입: (아이즈원 출신) 최예나, 권은비, 조유리, (우주소녀 출신) 다영
무명·소형 기획사·트로트·해외 K-pop은 1군 외.

동명이인 주의: 한국 아이돌 이름은 동명이인이 흔합니다(다영/지수/유나/하니 등).
메타데이터만으로 인물 식별이 100% 확실하지 않으면 1군 그룹 멤버 가능성을 우선
고려하고 "(추정)"으로 표시하세요.

# 좋은 후보 / 나쁜 후보 신호

좋은 직접 후보: 1군 인물 직접 등장, 의문형/부정형 제목, 시의성 낮고 반복 가능,
  8개 소재 유형에 명확히 속함, 분석 각도 존재, 한국어 제목.

나쁜 후보: 영어 제목, 직캠/뮤비/무대 본방 그 자체, 특정 시점에만 의미가 있는 휘발성 가십,
  콘텐츠의 주인공이 무명 아이돌·일반 유튜버 중심인 경우, 자극·낚시 톤,
  분석 각도와 재사용 가능한 기획 포인트가 모두 없음.

단순 짤·밈·리액션·모음형이라는 이유만으로 자동 탈락시키지 않습니다.
반복 가능한 제목 구조, 강한 훅, 멤버 페르소나, 관계성, 팬덤 반응 구조 등
털어드림식으로 변형 가능한 기획 포인트가 있다면 벤치마크 가치가 있는 것으로 판단합니다.

지시받은 출력 형식(JSON)을 정확히 따르세요. JSON 외 텍스트는 출력하지 마세요.

# 벤치마크 평가 원칙

벤치마크에서는 "털어드림에 그대로 사용할 수 있는가"와
"소재·훅·구성 방식을 털어드림식으로 변형할 가치가 있는가"를 구분해서 판단합니다.

단순 밈·리액션·모음형 콘텐츠라도,
반복 가능한 제목 구조, 강한 첫 3초 훅, 멤버 페르소나,
관계성, 팬덤 반응 구조 등 재사용 가능한 기획 포인트가 명확하면
벤치마크 가치는 있다고 판단할 수 있습니다.

단, weekly 실제 후보 선정 기준은 별개이며,
벤치마크 가치가 있다고 해서 실제 후보로 자동 채택되는 것은 아닙니다.
"""


# ============================================================================
# analyze_video user prompt 마지막에 붙는 target 별 지시.
#   · 기존 코드의 마지막 "teoldeurim_angle" 지시 문안을 target_angle 로 리네임.
#   · fit 축 / bv 축 / meta 필드는 공통 골격.
# ============================================================================
_ANALYZE_USER_PROMPT_INTRO = "다음 YouTube Shorts가 '털어드림' 채널에서 다룰 만한 후보인지 평가하세요."
_ANALYZE_USER_PROMPT_ANGLE = (
    "- target_angle: 털어드림식 2차/3차 해석으로 어떻게 변형할지 한 줄"
)
_ANALYZE_USER_PROMPT_ANCHOR_HIGH = (
    "60~79: 털어드림식으로 변형 가능한 훅·제목 구조·관계성·페르소나 포인트가 명확"
)


# ============================================================================
# analyze_patterns 대상 이름 문구
# ============================================================================
_PATTERN_USER_LEAD = (
    "아래는 '털어드림' 벤치마크 분석 대상으로 선별된 타 채널 인기 Shorts입니다."
)
_PATTERN_DIRECT_LABEL = "털어드림 직접 활용도가 높은 영상(fit_score 높은 후보)들의 공통 구조."


TARGET = {
    # ─────────────────────────────────────────
    # 기본 정보
    # ─────────────────────────────────────────
    "slug": "teoldeurim",
    "display_name": "털어드림",

    # ─────────────────────────────────────────
    # Claude 분석에 주입되는 identity + prompt 조각
    # ─────────────────────────────────────────
    "identity": {
        "channel_context": (
            "K-pop 연예인 중심의 이슈·비하인드·관계성 분석형 Shorts 채널. "
            "1차(이슈 자체) → 2차(반응 정리·해석) → 3차(여론·산업 영향) 로 재가공하는 "
            "'시의성 지연' 구조를 추구합니다."
        ),
        # HTML 렌더에서 표시되는 라벨 (target_angle 필드의 사람용 이름)
        "angle_field_label": "털어드림식 변형 각도",
        # Claude analyze_video system prompt (전문)
        "analyze_video_system_prompt": _ANALYZE_SYSTEM_PROMPT,
        # analyze_video user prompt 상단 문구
        "analyze_video_user_prompt_intro": _ANALYZE_USER_PROMPT_INTRO,
        # analyze_video 마지막 target_angle 지시
        "analyze_video_user_prompt_angle": _ANALYZE_USER_PROMPT_ANGLE,
        # anchor 60~79 문구 (target 별 표현이 다름)
        "analyze_video_anchor_high": _ANALYZE_USER_PROMPT_ANCHOR_HIGH,
        # analyze_patterns user prompt 문구
        "pattern_user_lead": _PATTERN_USER_LEAD,
        "pattern_direct_label": _PATTERN_DIRECT_LABEL,
    },

    # ─────────────────────────────────────────
    # 소프트 가이던스 — Hard rule 로 승격하지 않음.
    # 이 target 은 기존 시스템에서 명시적 soft signal 를 사용하지 않았으므로 빈 리스트.
    # (묘한덕질/짤덕방 은 별도 문안 부여)
    # ─────────────────────────────────────────
    "soft_guidance": {
        "positive_signals": [],
        "negative_signals": [],
    },

    # ─────────────────────────────────────────
    # 참고 채널 — 대표님 확정 7개
    # ─────────────────────────────────────────
    "reference_channels": [
        {"name": "패션탐정냥", "channel": "https://www.youtube.com/@tamjeongcat"},
        {"name": "덕칼럼", "channel": "https://www.youtube.com/channel/UC0l_Io9P2rTbM82PoC9Bi4w"},
        {"name": "팝픽", "channel": "https://www.youtube.com/@PopPickkk"},
        {"name": "아이소", "channel": "https://www.youtube.com/@iso-l6i2n"},
        {"name": "초미녀 갤러리", "channel": "https://www.youtube.com/@초미녀갤"},
        {"name": "케이팝과몰입러", "channel": "https://www.youtube.com/@K팝과몰입러"},
        {"name": "돌아에몽", "channel": "https://www.youtube.com/@돌아에몽dolahaemon"},
    ],

    # ─────────────────────────────────────────
    # 모드별 필터·상한 값
    #   Weekly: 매주 금 발송용. 기존 recent 프로파일 값 유지.
    #   Standard: 격주 금 별도 메일. 기존 standard 프로파일 값.
    #   MAX_VIEWS_INCLUSIVE=True (2026-08-25 확정: 상한값 이하 PASS).
    # ─────────────────────────────────────────
    "modes": {
        "weekly": {
            "SORT_BY": "NEWEST",
            "MAX_SHORTS_PER_CHANNEL": 50,
            "MIN_VIEWS": 50_000,
            "MAX_VIEWS": 3_000_000,
            "MAX_VIEWS_INCLUSIVE": True,
            "MIN_DURATION_SEC": 15,
            "MAX_DURATION_SEC": 180,
            "MIN_AGE_DAYS_EXCLUSIVE": None,
            "UPLOADED_WITHIN_DAYS": 7,
            "MAX_ANALYSIS_CANDIDATES": 30,
            "FINAL_CANDIDATES": 5,
            "PRE_ANALYSIS_DEDUP": True,      # 기존 Recent 순서 유지
            "ANALYSIS_MODEL": "claude-opus-4-7",
        },
        "standard": {
            "SORT_BY": "POPULAR",
            "MAX_SHORTS_PER_CHANNEL": 50,
            "MIN_VIEWS": 500_000,
            "MAX_VIEWS": 9_000_000,
            "MAX_VIEWS_INCLUSIVE": True,      # (2026-08-25) exclusive → inclusive 로 통일
            "MIN_DURATION_SEC": 15,
            "MAX_DURATION_SEC": 180,
            "MIN_AGE_DAYS_EXCLUSIVE": 30,     # exact 30d FAIL, 30d + 1s PASS
            "UPLOADED_WITHIN_DAYS": 365,      # exact 365d PASS, 365d + 1s FAIL
            "MAX_ANALYSIS_CANDIDATES": 30,
            "FINAL_CANDIDATES": 5,
            "PRE_ANALYSIS_DEDUP": False,      # 기존 Standard 순서 (Claude 분석 후 dedup)
            "ANALYSIS_MODEL": "claude-opus-4-7",
        },
    },

    # ─────────────────────────────────────────
    # sent history namespace 및 legacy dedup 소스
    # ─────────────────────────────────────────
    "history_namespace": "teoldeurim",
    "history_shared_across_modes": True,
    # 신규 저장 위치: benchmark/history/sent/teoldeurim/*.json
    #
    # 아래 legacy 소스는 "읽기 전용" 으로 dedup 에만 참고 (물리적 이동 금지).
    #   1) benchmark/history/sent/YYYY-MM-DD_{standard,recent}.json  ← 기존 flat 파일
    #   2) history/YYYY-MM-DD.json / history/YYYY-MM-DD_recent.json ← weekly.py 유산
    "legacy_sent_history_flat": True,
    "legacy_weekly_history": True,
}
