# 3-target Benchmark 자동 메일링 (털어드림 / 묘한덕질 / 짤덕방)

K-pop Shorts 채널 **털어드림 · 묘한덕질 · 짤덕방** 세 채널의 콘텐츠 소싱을 자동화하는 공용 Benchmark 엔진.

- **Weekly Bundle** — 매주 금요일 08:17 KST · 3 target 각각 Benchmark 리포트를 생성해 첨부 3개를 **한 통의 메일**로 발송.
- **털어드림 Standard** — 격주 금요일 08:23 KST (ISO 홀수 주차) · 털어드림 전용 · 별도 메일 1통 (첨부 1개).
- **공용 엔진 · target profile 분리** — `scripts/benchmark.py` 하나가 `config/targets/{slug}.py` 를 target profile 로 로드해 identity · Reference 채널 · Hard filter · Claude prompt 를 주입받아 target 무관하게 동작.

> 📌 Claude Code로 이 프로젝트를 이어서 작업한다면 **`CLAUDE.md`** 를 먼저 읽으세요. `docs/` 하위 문서는 legacy 시점 기준이므로 참고 정도로만.

---

## 주요 기능

### 매주 금 Weekly Bundle (`weekly_bundle.yml`)

- **cron**: `17 23 * * 4` (목 23:17 UTC = **금 08:17 KST**)
- **파이프라인**: `send_report.py --mode=weekly-bundle`
  1. `benchmark.py` 를 target=teoldeurim / myohanduk / jjalduk 각각 `MODE=weekly` 로 순차 실행
  2. 3 target 모두 성공 시에만 Gmail 1회 · 첨부 3개 (실패 target 이 하나라도 있으면 발송 skip · sent history 기록 skip)
  3. 발송 성공 후 각 target namespace 에 sent history 자동 저장
- **workflow_dispatch inputs**: `recipient_override` / `report_date` / `notice` / `reissue` / `force_rescan`

### 격주 금 털어드림 Standard (`weekly.yml`)

- **cron**: `23 23 * * 4` (목 23:23 UTC = **금 08:23 KST**) · ISO 홀수 주차만 실행
- **파이프라인**: `send_report.py --mode=friday` — target=teoldeurim + MODE=standard 단독 실행 → HTML 그대로 발송
- Weekly Bundle 과 concurrency group `teoldeurim-mailer` 공유 → Bundle 완료 후 Standard 순차 실행

### Target 별 조건 요약

| target | mode | 조회수 (inclusive) | 업로드 age | 길이 | Reference 채널 |
|---|---|---|---|---|---|
| 털어드림 | weekly | 50,000 ~ 3,000,000 | 0 ~ 7일 | 15 ~ 180초 | 7 |
| 털어드림 | standard | 500,000 ~ 9,000,000 | 30일 초과 ~ 365일 이내 | 15 ~ 180초 | 7 |
| 묘한덕질 | weekly | 100,000 ~ 3,000,000 | 0 ~ 7일 | 20초 이상 (상한 없음) | 6 |
| 짤덕방 | weekly | 100,000 ~ 3,000,000 | 0 ~ 7일 | 20초 이상 (상한 없음) | 3 |

- 모든 target/mode: `MAX_ANALYSIS_CANDIDATES=30` (padding 없는 upper cap · Hard 통과가 30 미만이면 그만큼만 분석), `FINAL_CANDIDATES=5` (`direct_final ≤ 5` + `benchmark_final ≤ 5` 각각의 cap · 두 리스트 상호 배타 · 실제 노출 0~10건).

### sent history · dedup

- **신규 저장 위치**: `benchmark/history/sent/{target}/YYYY-MM-DD_{mode}.json` (target namespace 분리)
- **털어드림 legacy backward-compat**:
  - `benchmark/history/sent/YYYY-MM-DD_{recent,standard}.json` (flat)
  - `history/YYYY-MM-DD.json` / `history/YYYY-MM-DD_recent.json` (legacy `weekly.py` 산출물)
- 털어드림은 신규 + legacy 를 합집합으로 dedup. **묘한덕질 · 짤덕방은 자기 namespace 만 참조** (legacy 미참조).
- 털어드림 Weekly ↔ Standard 는 같은 namespace 를 공유 → cross-mode dedup 유지.

### Apify Actor · 원본 title

- **액터 1개만 사용**: `streamers/youtube-shorts-scraper` (discovery-only).
  - Weekly Bundle 총 3 runs (target 당 1회) / Standard 1 run
- 이 액터가 응답에 `title` (유튜브 원본) + `translatedTitle` (번역) 을 함께 반환 (실측 검증).
- `normalize_video()` 에서 `title_original` / `title_translated` 로 분리 저장. Claude 프롬프트 · HTML 표시는 모두 `title_original` (한국어 원본) 사용.
- 2026-08-25~26 사이 시도했던 detail actor (`streamers/youtube-scraper`) 는 실측 결과 discovery 응답이 이미 원본을 제공하는 것이 확인되어 **완전 제거됨** (실행 시간·비용 각 50% 절감).

---

## 아키텍처

```
Common Benchmark Engine  (scripts/benchmark.py, target-agnostic)
  │  TARGET / MODE env → target profile 로드
  │
  ├─ config/targets/teoldeurim.py   identity + 7 refs + weekly/standard modes
  ├─ config/targets/myohanduk.py    identity + soft_guidance + 6 refs + weekly
  └─ config/targets/jjalduk.py      identity + soft_guidance + 3 refs + weekly

Orchestrator  (scripts/send_report.py)
  ├─ --mode=weekly-bundle → run_weekly_bundle : 3 target × mode=weekly → 1 mail · 3 첨부
  ├─ --mode=friday        → run_friday        : teoldeurim × mode=standard → 별도 1 mail
  └─ --mode=monday        → run_monday        : legacy (workflow 미사용)
```

**legacy 코드** (workflow 미사용 · 참고용으로만 유지):
- `scripts/weekly.py` — 이전 YouTube API 검색어 기반 파이프라인. 현재 `resend.yml` 만 사용.
- `.github/workflows/resend.yml` — 본인만 대상 · 최신 Standard history 재발송.

---

## 기술 스택

- **Python 3.11** · 표준 라이브러리 + `anthropic` SDK
- **Apify API** — `streamers/youtube-shorts-scraper` (Shorts discovery)
- **Claude API** — `claude-opus-4-7`
- **Gmail SMTP** — 리포트 발송 (첨부 다중 지원)
- **GitHub Actions** — cron 스케줄링 · 수동 실행

---

## 설치 · 실행

```bash
git clone https://github.com/seony-dev/teoldeurim-weekly.git
cd teoldeurim-weekly
python -m pip install -r requirements.txt
```

### 환경변수

`.env.example` 복사 → `.env` 로 값 채우기. **값은 절대 커밋 금지**.

| 변수 | 용도 |
|---|---|
| `APIFY_TOKEN` | Apify API 토큰 |
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `GMAIL_ADDRESS` | 발송용 Gmail 주소 |
| `GMAIL_APP_PASSWORD` | Gmail 앱 비밀번호 (16자리) |
| `RECIPIENT_EMAIL` | production 수신자 |
| `YOUTUBE_API_KEY` | (legacy) `weekly.py` 만 사용 · `resend.yml` 실행 시 필요 |

**실행 모드 env** (선택):
- `DRY_RUN=1` — Gmail 발송 skip + sent history 기록 skip (Apify · Claude 호출은 진행)
- `TEST_MODE=1` — subject 앞 `[TEST] ` prefix + production sent history 저장 skip (실 발송은 진행). 코드에 살아 있으나 **현재 운영 workflow yml 에서는 사용하지 않음** (2026-08-25~26 검증 단계에서 임시 활용 후 2026-08-26 운영 전환 시 제거됨). 필요 시 workflow yml 에 `TEST_MODE: '1'` env 를 추가하면 즉시 재활성.
- `TARGET=teoldeurim|myohanduk|jjalduk` · `MODE=weekly|standard` — benchmark.py 실행 시 target/mode 지정
- 기타 workflow_dispatch input: `FORCE_RESCAN` / `REPORT_DATE` / `NOTICE` / `REISSUE`

### GitHub Actions 시크릿

- **정식 운영**: `APIFY_TOKEN` / `ANTHROPIC_API_KEY` / `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` / `RECIPIENT_EMAIL`
- **검증용 재발송** (`resend.yml`): 위 + `RECIPIENT_EMAIL_SELF` + `YOUTUBE_API_KEY`

### 로컬 실행 · 검증

```bash
# Weekly Bundle DRY_RUN (실 Apify + Claude 호출, 메일·sent history skip)
DRY_RUN=1 python scripts/send_report.py --mode=weekly-bundle

# 개별 target 벤치마크 (실 Apify + Claude 호출)
TARGET=jjalduk MODE=weekly python scripts/benchmark.py

# 문법 체크 (비용 0)
python -c "import ast; ast.parse(open('scripts/benchmark.py',encoding='utf-8').read())"

# 전체 회귀 (비용 0, 순수 fixture)
for t in tests/test_*.py; do python "$t"; done
```

### GitHub 수동 실행

Actions 탭 → 워크플로 선택 → **Run workflow**

- `매주 금 Weekly Bundle (털어드림/묘한덕질/짤덕방)` — inputs: `recipient_override` / `report_date` / `notice` / `reissue` / `force_rescan`
- `털어드림 격주 Benchmark Standard (금요일)` — inputs: `force_rescan` / `report_date` / `notice` / `reissue`
- `털어드림 리포트 재발송 (본인만)` — legacy · 최신 Standard history 를 `RECIPIENT_EMAIL_SELF` 로 재발송

---

## 설정 바꾸기

| 대상 | 위치 |
|---|---|
| target Reference 채널 · Hard filter · Claude prompt | `config/targets/{slug}.py` |
| 우리 채널 · blocklist (모든 target 공용) | `config/benchmark_config.py` `EXCLUDE_CHANNELS` / `EXCLUDE_CHANNEL_IDS` |
| Apify Actor | `config/benchmark_config.py` `APIFY_DISCOVERY_ACTOR` |
| Weekly Bundle target 순서 | `scripts/send_report.py` `_WEEKLY_BUNDLE_TARGETS` |
| Claude 모델 | `config/targets/{slug}.py` mode 내 `ANALYSIS_MODEL` |

**주의**: 새 target 추가는 `config/targets/__init__.py` `_REGISTRY` 등록 + profile 파일 신설 + Weekly Bundle 포함 여부 결정.

---

## 테스트

- 10 스위트 · **422 assertion** (2026-08-26 기준)
- 실행: `python tests/test_{name}.py` 개별 또는 `for t in tests/test_*.py; do python "$t"; done`
- 대표 스위트:
  - `test_target_teoldeurim_regression.py` — target profile 이동 회귀
  - `test_new_targets_boundary.py` — 묘한덕질 · 짤덕방 Hard filter 경계
  - `test_title_enrichment.py` — discovery-only 구조 · known fixture (`cZwgBQHn740`)
  - `test_duration_render.py` — MAX_DURATION_SEC=None HTML 렌더
  - `test_weekly_bundle_mail.py` — 3첨부 1메일 · TEST_MODE · 부분 실패 skip
  - `test_orchestrator_dryrun.py` — subprocess 호출 구조 검증

---

## 비용 (Apify 예상 · Claude 별도)

**아래 금액은 Apify 부분만의 예상값이며 Claude API 비용은 별도입니다.** Standard 는 첫 정식 실행 (2026-08-28 예정) 전이므로 실측이 아닌 참고 예상값입니다.

### Apify (discovery-only 기준)

| 항목 | Apify 실행 | Apify 예상 (USD) |
|---|---|---|
| Weekly Bundle 1회 | discovery 3 runs (target 당 1회) | ~$2.32 (Weekly Bundle 첫 실측 · 2026-08-26 아침 실행 기준) |
| Standard 1회 | discovery 1 run | ~$1.05 (**Standard production 실측 전 · 참고값**) |
| Apify 월간 예상 (Weekly 4회 + Standard 2회) |  | **~$11 (Apify 만)** |

### Claude API (별도 · `claude-opus-4-7`)

분석 대상 후보 수 · 프롬프트 caching · 실제 Hard 통과 수에 따라 다릅니다. 잠정 실측 (2026-08-25 개별 profile DRY_RUN):

| target/mode | 실측 Claude 비용 |
|---|---|
| 짤덕방 weekly | ~$0.09 |
| 묘한덕질 weekly | ~$0.13 |
| 털어드림 weekly | 미측 (Weekly Bundle 정식 실행 후 청구서 확인 예정) |
| 털어드림 standard | 미측 (첫 실 운영 실행 대기 중) |

### 기타

- GitHub Actions · Gmail SMTP · Python 무료
- **실 청구액은 Apify / Anthropic 콘솔에서 별도 확인**
- Standard 월간 실 비용은 2026-08-28 첫 정식 실행 이후 재산정

---

## 보안 메모

- 시크릿은 GitHub Encrypted Secrets 및 로컬 `.env` (git 제외) 에만
- `.env` · API 키 · 앱 비번은 절대 커밋·로그·문서에 노출 금지
- 키 노출 시 즉시 폐기 후 재발급

---

## 문서

| 파일 | 내용 |
|---|---|
| `README.md` | 이 파일 |
| `CLAUDE.md` | Claude Code 작업 규칙 · AI로 작업 시 필독 |
| `.env.example` | 환경변수 템플릿 |
| `docs/*` | (legacy 시점) 인수인계 · 운영 가이드 · 작업 현황. 최신 상태는 이 README + `CLAUDE.md` 기준으로 판단. |
