# 털어드림 자동 메일링 + 타 채널 벤치마크

K-pop Shorts 채널 **'털어드림'**의 콘텐츠 소싱을 자동화하는 봇 모음.

- **주간 메일러 (`weekly.py` + `benchmark.py` 통합)** — 두 종류의 리포트를 자동 발송:
  - **격주 금요일 08:47 KST · Standard** — 30일 초과 ~ 365일 이내 K-pop Shorts 후보
    (ISO 홀수 주차만 실행)
  - **월·수·금 (전부 08:42 KST) · Recent** — 최근 0~7일 이내 신선한 콘텐츠. 금요일은
    Standard(08:47) 보다 5분 먼저 실행되어 recent 를 먼저 받아볼 수 있음
    (concurrency group `teoldeurim-mailer` 공유 → Recent 완료 후 Standard 자동 큐 실행)
- **오케스트레이터 (`send_report.py`)** — Weekly(YouTube 검색어 기반) + Benchmark(외부 참고
  채널) 을 각각 subprocess 로 실행 → 상위 2탭 shell HTML 로 통합 → Gmail 발송 → 결과
  `history/` 및 `benchmark/history/sent/` 에 영구 아카이브.
- **타 채널 벤치마크 (`benchmark.py` 단독)** — 외부 참고 채널 (수동 + history 기반 자동)의
  인기 Shorts 를 Apify 로 수집·분석해 "털어드림에 쓸 만한 참고 후보 + 기획 포인트" 리포트
  생성. Standard·Recent 두 프로파일 지원.

> 📌 Claude Code로 이 프로젝트를 이어서 작업한다면 먼저 **`CLAUDE.md`**를 읽으세요.
> 작업 현황은 `docs/WORK_STATUS.md`, 운영 가이드는 `docs/OPERATIONS.md`,
> 인수인계 문서는 `docs/HANDOFF.md`에 있습니다.

---

## 주요 기능

### 격주 금요일 Standard 리포트
- **cron**: `47 23 * * 4` (목 23:47 UTC = **금 08:47 KST**) — ISO 홀수 주차만 실행 (`weekly.yml`)
- **조건**: 조회수 **50만 ~ 900만**, 업로드 **30일 초과 ~ 365일 이내**, 길이 **15 ~ 180초**
- **workflow_dispatch inputs** (수동 실행 시): `force_rescan` / `report_date` (slot 날짜 override)
  / `notice` (본문 최상단 안내 박스) / `reissue` ([재발송] subject prefix)
- **파이프라인**: `send_report.py --mode=friday` → benchmark(standard) → weekly(standard) →
  통합 shell HTML → Gmail 발송 → 성공 시 `history/YYYY-MM-DD.json` + `benchmark/history/sent/YYYY-MM-DD_standard.json` 자동 커밋·push

### 월·수·금 Recent 리포트
- **cron 3개** (`monday_recent.yml`):
  - `42 23 * * 0` (일 23:42 UTC = **월 08:42 KST**)
  - `42 23 * * 2` (화 23:42 UTC = **수 08:42 KST**)
  - `42 23 * * 4` (목 23:42 UTC = **금 08:42 KST**) — 같은 날 Standard(08:47) 보다 5분 먼저 실행.
    concurrency group `teoldeurim-mailer` 로 Recent 완료 후 Standard 자동 큐 실행
- **조건**: 조회수 **5만 ~ 300만**, 업로드 **0 ~ 7일 이내**, 길이 **15 ~ 180초**
- **파이프라인**: `send_report.py --mode=monday` (backward compat 상 이름 유지, 실제로는 월·수·금 공용)

### 공통 필터 · dedup
- **Hard 필터** (코드 규칙) — 조회수 범위, Shorts 길이, 한국어 제목, 업로드 기간,
  채널·키워드 블록, 과거 발송 영상 자동 중복 제거
- **컷 사유 안전 집계** — `collections.Counter` 로 새 reason 코드 추가에도 KeyError 없음
- **Cross-profile dedup** — `history/*.json` (standard + `_recent`) + `benchmark/history/sent/*.json`
  모두 통합 로드 → 재발송 방지. 월·수·금 recent 파일은 실행일별 분리 저장 (`YYYY-MM-DD_recent.json`)
- **Soft 필터** (Claude AI 분석) — 후보별 소재 유형·훅·변형 주제 등 분석 + 채택/탈락 판정

### 타 채널 벤치마크 (`benchmark.py`)
1. 외부 참고 채널의 인기 Shorts 를 **Apify** (`streamers/youtube-shorts-scraper`) 로 수집
2. 참고 채널은 **수동 지정 7개** (config/benchmark_config.py) + `history/` 에서 자동 추출
   (weekly 가 자주 통과시킨 채널, `AUTO_REFERENCE_TOP_N` 로 상한 조절)
3. 우리 채널·제외 채널 필터링 → MAX_VIEWS 상한 분리 → Hard 필터 → Claude 분석
4. 결과물 (한 HTML 안에 2개 섹션): **① 참고 후보 리스트** / **② 기획 포인트 리포트**
5. Standard · Recent 두 프로파일 지원 (조건 값만 다름, weekly 와 완전 독립)

---

## 기술 스택

- **Python 3.11**
- **표준 라이브러리** — `urllib`, `smtplib`, `json`, `email` 등 (weekly.py는 외부 의존성 최소)
- **`anthropic`** SDK — Claude API (유일한 외부 의존성, `requirements.txt`)
- **YouTube Data API v3** — 주간 후보 수집
- **Claude API** (`claude-opus-4-7`) — 후보 AI 분석
- **Apify API** — 벤치마크 모듈의 외부 채널 Shorts 수집 (`streamers/youtube-shorts-scraper`)
- **Gmail SMTP** — 리포트 발송
- **GitHub Actions** — cron 스케줄링 / 수동 실행

---

## 설치 방법

```bash
git clone https://github.com/seony-dev/teoldeurim-weekly.git
cd teoldeurim-weekly
python -m pip install -r requirements.txt
```

Python 3.11 이상 필요. 의존성은 `anthropic` 하나뿐입니다.

---

## 환경변수 설정 방법

`.env.example`을 복사해 `.env`를 만들고 값을 채웁니다. **변수명만** 아래에 적습니다 — 값은 `.env`에만.

| 변수명 | 용도 | 사용 모듈 |
|---|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 키 | weekly |
| `GMAIL_ADDRESS` | 발송용 Gmail 주소 | weekly |
| `GMAIL_APP_PASSWORD` | Gmail 앱 비밀번호(16자리, 일반 비번 아님) | weekly |
| `RECIPIENT_EMAIL` | 리포트 수신 주소 | weekly |
| `ANTHROPIC_API_KEY` | Claude API 키 | weekly + benchmark |
| `APIFY_TOKEN` | Apify API 토큰 | benchmark |
| `RESEND_LATEST` / `DRY_RUN` / `FORCE_RESCAN` | 실행 모드 (선택) | weekly |

> `.env` 파일은 `.gitignore`로 git에서 제외됩니다. **절대 커밋하지 마세요.**

### GitHub Actions 시크릿

자동 실행을 쓰려면 레포 **Settings → Secrets and variables → Actions**에 시크릿을 등록합니다.

- `weekly.yml` (Standard) · `monday_recent.yml` (Recent) 공통: `YOUTUBE_API_KEY`,
  `APIFY_TOKEN`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `RECIPIENT_EMAIL`
- `resend.yml` 사용: 위 + `RECIPIENT_EMAIL_SELF` (재발송 시 본인에게만 보내는 주소)

**시크릿 등록 시 함정 (과거에 실제로 겪은 문제):**
- 이름은 대소문자·언더스코어까지 **정확히 일치** (`Youtube_Api_Key` ❌, `YOUTUBE_API_KEY` ✅)
- 값 끝에 줄바꿈·공백이 붙지 않게 주의 (키 복사 시 `\n`이 따라붙는 경우 흔함)
- `GMAIL_APP_PASSWORD`는 일반 비밀번호가 아니라 **16자리 앱 비밀번호**

---

## 실행 방법

### 로컬 실행 (주간 메일러)

```powershell
# Windows — .env를 읽어 환경변수로 주입 후 weekly.py 실행
./run_local.ps1
```
```bash
# macOS / Linux — 환경변수를 직접 export 후 실행
python scripts/weekly.py
```

⚠️ 일반 실행은 **실제로 메일을 발송**하고 API 과금이 발생합니다.
메일 없이 결과만 보려면 `DRY_RUN=1`을 설정하세요 (`docs/OPERATIONS.md` 참고).

### 벤치마크 모듈 실행

```bash
python scripts/benchmark.py
```
⚠️ Apify(유료) + Claude API 과금이 발생합니다. 첫 실행은 `config/benchmark_config.py`의
값을 낮춰 작게 돌리세요.

### GitHub에서 수동 실행

레포 **Actions** 탭 → 워크플로 선택 → **Run workflow**
- `털어드림 격주 통합 리포트 (금요일)` (weekly.yml) — Standard 실행. inputs:
  `force_rescan` / `report_date` (예: `2026-07-17` 슬롯 재발송 시) / `notice` (본문 상단
  안내 문구, literal `\n` → 개행) / `reissue` (`[재발송]` subject prefix)
- `털어드림 최근 콘텐츠 리포트 (월·수·금)` (monday_recent.yml) — Recent 실행. inputs 없음
- `털어드림 리포트 재발송 (본인만)` (resend.yml) — 최신 Standard history 를 본인에게만 재발송

---

## 테스트 방법

| 방법 | 명령 | 비용 |
|---|---|---|
| 문법 체크 | `python -c "import ast; ast.parse(open('scripts/weekly.py',encoding='utf-8').read())"` | 없음 |
| 미리보기 (메일 X) | `DRY_RUN=1` 설정 후 `python scripts/weekly.py` → `local_output/preview_*.html` 확인 | API 과금 O |
| 벤치마크 소규모 테스트 | `config/benchmark_config.py` 값을 낮춰 실행 | 소액 과금 |

상세 내용은 `LOCAL_TEST.md` 및 `docs/OPERATIONS.md` 참고.

---

## 배포/운영 방법

별도 서버 배포 없음 — **GitHub Actions cron**으로 운영됩니다.

- **Standard**: `.github/workflows/weekly.yml`의 `cron: '47 23 * * 4'` (목 23:47 UTC = 금 08:47 KST).
  ISO 홀수 주차만 실행 (`workflow_dispatch` 수동 실행은 격주 gate 우회).
- **Recent (월·수·금)**: `.github/workflows/monday_recent.yml` 의 cron 3개 (일·화·목 23:42 UTC =
  월·수·금 08:42 KST).
- 두 workflow 는 `concurrency: group: teoldeurim-mailer` 를 공유 → 금요일 순서: **Recent(08:42)
  먼저 실행 → 완료 후 Standard(08:47) 실행** (5분 차 트리거지만 concurrency 대기 로 순차 보장).
  push race 원천 차단.
- 코드를 `main` 에 push 하면 **다음 실행부터 즉시 반영**됩니다 (검증 없이 push 금지).
- 실행 후 GitHub Actions 가 `history/` 및 `benchmark/history/sent/` 를 자동 커밋·push 합니다
  (`git pull --rebase --autostash` 로 다른 workflow 의 push 를 먼저 반영).

> GitHub cron 정책: 60일간 레포에 변경이 없으면 cron이 자동 정지됨.
> 이 봇은 매주 history를 커밋하므로 정상 운영 중에는 멈추지 않습니다.

---

## 설정 바꾸기

- **Weekly 공통 설정** — `scripts/weekly.py` 상단의 `CONFIG` 딕셔너리
  (`MIN_VIEWS`, `MAX_VIEWS`, `MAX_VIEWS_INCLUSIVE`, `MAX_DURATION_SEC`, `MIN_DURATION_SEC`,
  `LOOKBACK_DAYS_*`, `CHANNEL_BLOCKLIST`, `CHANNEL_ID_BLOCKLIST`, `TITLE_KEYWORD_BLOCKLIST`,
  `SEARCH_QUERIES` 등)
- **Weekly 프로파일 override** — `WEEKLY_PROFILES["standard" | "recent"]` — 프로파일별 값만
  덮어씀 (검색어 등 공통 필드는 CONFIG 상속)
- **AI 분석 기준** — `weekly.py`의 `ANALYSIS_SYSTEM_PROMPT`
- **벤치마크 설정** — `config/benchmark_config.py` 의 `BENCHMARK_CONFIG` + `BENCHMARK_PROFILES`
  (참고 채널 추가/삭제, MIN_VIEWS/MAX_VIEWS/UPLOADED_WITHIN 등)
- 수정 후 push 하면 다음 실행부터 적용됩니다 — Standard 조건 변경은 특히 신중히
  (금요일 정기 발송 결과에 즉시 영향).

---

## 자주 발생하는 문제와 해결

| 증상 | 원인 / 해결 |
|---|---|
| `필수 환경변수 누락` | `.env` 또는 GitHub 시크릿에서 변수명·값 확인 |
| `❌ YOUTUBE_API_KEY: 등록 안 됨` | 시크릿 이름을 정확히 등록 (대소문자 주의) |
| `invalid x-api-key` | YouTube API 키 무효 — Google Cloud Console에서 활성/제한 확인 |
| `(535, ... Username and Password not accepted)` | Gmail 앱 비밀번호 오류 — 16자리 앱 비번 사용 |
| `quotaExceeded` | YouTube API 일일 한도(10,000 유닛) 초과 — 다음날 재시도 |
| 메일은 오는데 후보 0개 | 그 주에 조건 통과 영상이 없었던 것 — 일시적 현상 |
| PowerShell 한글 깨짐 | `run_local.ps1`은 UTF-8 BOM으로 저장돼야 함 |
| cron이 멈춤 | 60일 비활성으로 정지 — Actions 탭에서 수동 실행 1회 |

---

## 비용

- **GitHub Actions** — 회당 약 2분, 무료 한도(월 2,000분) 내
- **YouTube Data API** — 회당 약 2,400~4,800 유닛, 일일 한도(10,000) 내
- **Gmail SMTP** — 무료
- **Claude API** — ⚠️ **과금 발생**. 주간 후보 분석 비용은 분석 후보 수에 비례
  (프롬프트 캐싱 적용 시 회당 수십~수백 원 수준). `[확인 필요]` — 실제 운영 청구액은 Anthropic 콘솔에서 확인.
- **Apify** — ⚠️ **과금 발생** (벤치마크 모듈, pay-per-event). 실행 빈도·수집량에 비례.

---

## 보안 메모

- 시크릿은 GitHub의 암호화 저장소에 보관 — 코드/로그에 평문 노출되지 않음
- `.env`는 git에서 제외됨 — 절대 커밋 금지
- 앱 비밀번호·API 키는 각 서비스 콘솔에서 언제든 폐기/재발급 가능
- ⚠️ 키가 노출된 적이 있다면 즉시 폐기하고 재발급할 것

---

## 문서 구조 안내

| 파일 | 내용 |
|---|---|
| `README.md` | 프로젝트 소개·설치·실행 (이 파일) |
| `CLAUDE.md` | Claude Code 작업 규칙 — **AI로 작업 시 가장 먼저 읽을 것** |
| `LOCAL_TEST.md` | 로컬 테스트 메모 |
| `.env.example` | 환경변수 템플릿 |
| `docs/WORK_STATUS.md` | 현재 작업 현황·남은 작업 |
| `docs/OPERATIONS.md` | 운영·실행·비용·트러블슈팅 가이드 |
| `docs/HANDOFF.md` | 인수인계 문서 (배경·의사결정·리스크) |
