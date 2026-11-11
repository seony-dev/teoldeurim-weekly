# Benchmark 자동 메일링 (털어드림 / 묘한덕질)

K-pop Shorts 채널 **털어드림 · 묘한덕질** 의 콘텐츠 소싱을 자동화하는 공용 Benchmark 엔진.
(짤덕방 target profile 은 보존되어 있으나 2026-11-11 부터 Weekly Bundle 대상에서 제외.)

- **정기 실행** — 매주 금요일 09:10 KST · Cloud Scheduler → Cloud Run Job `teoldeurim-friday-prod` (2026-11-09 GitHub Actions 에서 이전).
- **Weekly Bundle** — 매주 · 털어드림 · 묘한덕질 2 target 각각 Benchmark 리포트를 생성해 첨부 2개를 **한 통의 메일**로 발송.
- **털어드림 Standard** — 같은 실행에서 Weekly Bundle 완료 후 · KST ISO 홀수 주차만 · 털어드림 전용 · 별도 메일 1통 (첨부 1개).
- **공용 엔진 · target profile 분리** — `scripts/benchmark.py` 하나가 `config/targets/{slug}.py` 를 target profile 로 로드해 identity · Reference 채널 · Hard filter · Claude prompt 를 주입받아 target 무관하게 동작.

> 📌 Claude Code로 이 프로젝트를 이어서 작업한다면 **`CLAUDE.md`** 를 먼저 읽으세요. `docs/` 하위 문서는 legacy 시점 기준이므로 참고 정도로만.

---

## 주요 기능

### 정기 실행 (GCP · 2026-11-09 cutover)

- **Cloud Scheduler** `teoldeurim-friday-prod-schedule` — `10 9 * * 5` (`Asia/Seoul`) = **매주 금 09:10 KST**
- → **Cloud Run Job** `teoldeurim-friday-prod` (`asia-northeast3`) · 인자 없음 = `scripts/friday_orchestrator.py --mode auto`
- **auto mode** (한 execution 안에서 순차):
  1. GCS execution lock 획득 — 같은 날짜로 이미 실행 중이면 중복으로 보고 정상 종료
  2. **Weekly Bundle** (`send_report.py --mode=weekly-bundle`) — 매주
  3. Weekly 가 완료(또는 이미 완료)된 경우에만 KST ISO 주차 판정 → **홀수 주차면 털어드림 Standard** (`send_report.py --mode=friday`), 짝수 주차면 skip
  4. Weekly 가 실패 / ambiguous 이면 Standard 는 실행하지 않음
- 실행 상태 (lock · status · completed marker) 와 sent history 는 GCS (`SENT_HISTORY_BACKEND=gcs`) · `STATE_NAMESPACE=prod` 기준
- 명시 mode: `--mode=weekly-bundle` (Weekly 만) / `--mode=friday` (Standard 만 · 격주 판정 없음) — execution 별 `--args` 로 지정
- **테스트 Job** `teoldeurim-friday-test` — 같은 이미지 · `TEST_MODE=1` · `STATE_NAMESPACE=test` (prod 상태·sent history 와 분리)

```bash
# ⚠️ 아래 명령은 실 Apify · Claude 과금 + 실 메일 발송이 발생 — 사용자 확인 후에만
gcloud run jobs execute teoldeurim-friday-test --region=asia-northeast3
gcloud run jobs execute teoldeurim-friday-prod --region=asia-northeast3 --args=--mode=friday
```

### Weekly Bundle (`send_report.py --mode=weekly-bundle`)

- **실행**: 매주 금 정기 실행 (auto mode 1단계)
- **파이프라인**:
  1. `benchmark.py` 를 target=teoldeurim / myohanduk 각각 `MODE=weekly` 로 순차 실행 (jjalduk 은 2026-11-11 부터 제외)
  2. 모든 target 성공 시에만 Gmail 1회 · 첨부 2개 (실패 target 이 하나라도 있으면 발송 skip · sent history 기록 skip)
  3. 발송 성공 후 각 target namespace 에 sent history 자동 저장

### 털어드림 Standard (`send_report.py --mode=friday`)

- **실행**: 매주 금 정기 실행에서 Weekly Bundle 완료 후 · KST ISO 홀수 주차만 (격주)
- **파이프라인**: target=teoldeurim + MODE=standard 단독 실행 → HTML 그대로 발송

### Target 별 조건 요약

| target | mode | 조회수 (inclusive) | 업로드 age | 길이 | Reference 채널 |
|---|---|---|---|---|---|
| 털어드림 | weekly | 50,000 ~ 3,000,000 | 0 ~ 7일 | 15 ~ 180초 | 7 |
| 털어드림 | standard | 500,000 ~ 9,000,000 | 30일 초과 ~ 365일 이내 | 15 ~ 180초 | 7 |
| 묘한덕질 | weekly | 100,000 ~ 3,000,000 | 0 ~ 7일 | 20초 이상 (상한 없음) | 6 |
| 짤덕방 (Weekly Bundle 제외 · profile 보존) | weekly | 100,000 ~ 3,000,000 | 0 ~ 7일 | 20초 이상 (상한 없음) | 3 |

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
  - Weekly Bundle 총 2 runs (target 당 1회) / Standard 1 run
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
  └─ config/targets/jjalduk.py      identity + soft_guidance + 3 refs + weekly (Weekly Bundle 제외 · 보존)

Friday Orchestrator  (scripts/friday_orchestrator.py · Cloud Run Job 진입점)
  └─ --mode auto (기본) : GCS lock → weekly-bundle → (KST ISO 홀수 주) friday

Orchestrator  (scripts/send_report.py)
  ├─ --mode=weekly-bundle → run_weekly_bundle : teoldeurim·myohanduk × mode=weekly → 1 mail · 2 첨부
  ├─ --mode=friday        → run_friday        : teoldeurim × mode=standard → 별도 1 mail
  └─ --mode=monday        → run_monday        : legacy (workflow 미사용)
```

**legacy 코드** (정기 실행 미사용 · 참고용으로만 유지):
- `scripts/weekly.py` — 이전 YouTube API 검색어 기반 파이프라인. 현재 `resend.yml` 만 사용.
- `.github/workflows/resend.yml` — 본인만 대상 · 최신 Standard history 재발송.

---

## 기술 스택

- **Python 3.11** · 표준 라이브러리 + `anthropic` SDK
- **Apify API** — `streamers/youtube-shorts-scraper` (Shorts discovery)
- **Claude API** — `claude-opus-4-7`
- **Gmail SMTP** — 리포트 발송 (첨부 다중 지원)
- **GCP** (2026-11-09~) — Cloud Scheduler (정기 트리거) · Cloud Run Job (실행) · GCS (실행 상태 · sent history) · Secret Manager (시크릿)
- **GitHub Actions** — 정기 실행 미사용 (`schedule:` 트리거 없음) · legacy `resend.yml` 수동 실행만 가능

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
- `TEST_MODE=1` — subject 앞 `[TEST] ` prefix + production sent history 저장 skip (실 발송은 진행). 현재 Cloud Run 테스트 Job `teoldeurim-friday-test` 에 `STATE_NAMESPACE=test` 와 함께 설정됨 (prod Job 에는 없음).
- `TARGET=teoldeurim|myohanduk|jjalduk` · `MODE=weekly|standard` — benchmark.py 실행 시 target/mode 지정
- 기타: `FORCE_RESCAN` / `REPORT_DATE` / `NOTICE` / `REISSUE` (이전 GitHub workflow_dispatch input 과 같은 env)

### 시크릿

- **Cloud Run Job (정기 운영 · prod/test)**: Secret Manager 참조 — `APIFY_TOKEN` / `ANTHROPIC_API_KEY` / `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` / `RECIPIENT_EMAIL`
- **GitHub Actions 시크릿**: 위 5개 + `RECIPIENT_EMAIL_SELF` / `YOUTUBE_API_KEY` 등록 유지. 현재 실제로 실행 가능한 workflow 는 legacy `resend.yml` 뿐

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

### GitHub Actions (2026-11-09 이후 정기 실행 미사용)

- 모든 workflow 에서 `schedule:` 트리거 제거 — 정기 실행은 위 **정기 실행 (GCP)** 참고.
- `weekly_bundle.yml` (`매주 금 Weekly Bundle (털어드림/묘한덕질)`) · `weekly.yml` (`털어드림 격주 Benchmark Standard (금요일)`) — `workflow_dispatch` 버튼은 남아 있지만 `manual-guard` job 이 안내 메시지 출력 후 **실패 처리** (리포트 생성·메일 발송 없음). 기존 job 정의는 Cloud Run 수동 실행 이전 작업 참고용으로 보존 (`if: ${{ false }}`).
- `resend.yml` (`털어드림 리포트 재발송 (본인만)`) — legacy · guard 없음 · 최신 `history/` Standard 를 `RECIPIENT_EMAIL_SELF` 로 재발송.
- 수동으로 리포트를 다시 돌려야 하면 GitHub 대신 Cloud Run Job 을 execute (위 명령 · 과금·발송 주의).

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
  - `test_weekly_bundle_mail.py` — 2첨부 1메일 · TEST_MODE · 부분 실패 skip
  - `test_weekly_bundle_targets.py` — Weekly Bundle 대상 = 털어드림·묘한덕질 (짤덕방 제외) · mode 별 실행
  - `test_orchestrator_dryrun.py` — subprocess 호출 구조 검증

---

## 비용 (Apify 예상 · Claude 별도)

**아래 금액은 Apify 부분만의 예상값이며 Claude API 비용은 별도입니다.** 주별 참고 채널 업로드량에 따라 실제 수집 item 수가 달라지므로 ±20% 수준 변동 가능.

### Apify (discovery-only · Weekly 는 서버측 8일 pre-filter 적용 후)

| 항목 | Apify 실행 | Apify 예상 (USD) |
|---|---|---|
| Weekly Bundle 1회 (이전 · pre-filter 없음) | discovery 3 runs | ~$2.45 (10/02·10/09 실측) |
| **Weekly Bundle 1회 (현재 · 8일 pre-filter 적용)** | discovery 2 runs + `oldestPostDate` (2026-11-11 짤덕방 제외) | **≤ ~$0.36 (2026-10-13 3 runs 실측 기준)** |
| Standard 1회 | discovery 1 run | ~$1.05 (2026-10-09 실측) |
| **Apify 월간 예상 (Weekly 4회 + Standard 2회 합산 · 전체)** |  | **~$3.5** (참고: pre-filter 적용 전 전체 ~$11.8) |

> `oldestPostDate` 는 Weekly profile 에만 적용되는 서버측 1차 날짜 필터로, Python Hard filter 의 정확한 7일 조건은 그대로 유지됩니다. 상세 설계는 `config/targets/*.py` 의 `APIFY_COLLECTION_LOOKBACK_DAYS` 참고.

### Claude API (별도 · `claude-opus-4-7`)

분석 대상 후보 수 · 프롬프트 caching · 실제 Hard 통과 수에 따라 다릅니다. 잠정 실측 (2026-08-25 개별 profile DRY_RUN):

| target/mode | 실측 Claude 비용 |
|---|---|
| 짤덕방 weekly | ~$0.09 |
| 묘한덕질 weekly | ~$0.13 |
| 털어드림 weekly | 미측 (Weekly Bundle 정식 실행 후 청구서 확인 예정) |
| 털어드림 standard | 미측 (첫 실 운영 실행 대기 중) |

### 기타

- Gmail SMTP · Python 무료
- Cloud Run · Cloud Scheduler · GCS · Secret Manager 사용료는 GCP 콘솔에서 확인 (확인 필요)
- **실 청구액은 Apify / Anthropic 콘솔에서 별도 확인**

---

## 보안 메모

- 시크릿은 GCP Secret Manager (Cloud Run) · GitHub Encrypted Secrets · 로컬 `.env` (git 제외) 에만
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
