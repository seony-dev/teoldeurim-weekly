# CLAUDE.md — Claude Code 작업 규칙

> 이 파일은 Claude Code가 `teoldeurim_weekly` 프로젝트를 열 때 **가장 먼저** 읽어야 하는 작업 규칙 문서입니다.
> 새 계정 / 새 세션에서도 이 문서만 보면 바로 이어서 작업할 수 있도록 작성됐습니다.

---

## 1. 프로젝트 목적

K-pop Shorts 채널 **'털어드림'**의 콘텐츠 소싱을 자동화하는 봇 모음입니다.

- **weekly.py** — 매주 금요일 YouTube에서 K-pop Shorts 후보를 자동 수집·AI 분석하여 이메일로 발송하고 결과를 아카이브.
- **benchmark.py** — (신규/보조) 외부 참고 채널의 인기 Shorts를 수집해 "털어드림에 쓸 만한 참고 후보 + 기획 포인트" 리포트를 생성.

두 모듈은 **독립적으로 동작**하며, benchmark는 weekly를 보완하는 보조 도구입니다.

---

## 2. 현재 프로젝트 상태 요약

- `weekly.py` — **운영 중**. 매주 금 09:00 KST GitHub Actions 자동 실행. 안정 상태.
- `benchmark.py` + `config/benchmark_config.py` — **신규 추가, 테스트 단계**.
  - 아직 git에 커밋되지 않은 untracked 파일.
  - `config/benchmark_config.py`는 현재 **테스트값**이 들어가 있음 (운영 전 원복 필요 — `[테스트값]` 주석 참고).
  - 전용 GitHub Actions 워크플로(`benchmark.yml`)는 **아직 미작성**.
- 상세 현황은 `docs/WORK_STATUS.md` 참고.

---

## 3. 주요 실행 명령

| 목적 | 명령 | 비용/위험 |
|---|---|---|
| 주간 봇 로컬 실행 | `./run_local.ps1` (Windows) 또는 `python scripts/weekly.py` | ⚠️ 메일 발송 + API 과금 |
| 주간 봇 — 메일 없이 미리보기 | `.env`/환경변수에 `DRY_RUN=1` 후 실행 | API 과금 O, 메일 발송 X |
| 주간 봇 — 최신 history 재발송만 | `RESEND_LATEST=1` 후 실행 | 메일 발송 O, 수집·분석 X |
| 벤치마크 모듈 실행 | `python scripts/benchmark.py` | ⚠️ Apify 과금 + Claude API 과금 |
| 문법 체크 (안전) | `python -c "import ast; ast.parse(open('scripts/weekly.py',encoding='utf-8').read())"` | 없음 |

> 실행 모드 상세는 `docs/OPERATIONS.md` 참고.

---

## 4. 주요 폴더/파일 구조

```
teoldeurim_weekly/
├── CLAUDE.md                    ← 이 파일 (작업 규칙)
├── README.md                    ← 프로젝트 소개·세팅 가이드
├── LOCAL_TEST.md                ← 로컬 테스트 메모 (기존)
├── .env                         ← 비밀값 (git 제외, 절대 출력 금지)
├── .env.example                 ← 환경변수 템플릿 (값 없음)
├── requirements.txt             ← 의존성 (anthropic SDK 하나)
├── run_local.ps1                ← 로컬 실행 스크립트 (.env 로드 → weekly.py)
├── scripts/
│   ├── weekly.py                ← 주간 메일러 (메인, ~2,250줄)
│   └── benchmark.py             ← 타 채널 벤치마크 모듈 (신규)
├── config/
│   └── benchmark_config.py      ← 벤치마크 설정 (benchmark.py 전용)
├── history/
│   └── YYYY-MM-DD.json          ← weekly 결과 아카이브 (git 추적됨, dedup 기준)
├── benchmark/
│   ├── YYYY-MM-DD_filtered_raw.json   ← 벤치마크 수집 데이터
│   └── YYYY-MM-DD_report.html        ← 벤치마크 리포트
├── .github/workflows/
│   ├── weekly.yml               ← 주간 cron 워크플로
│   └── resend.yml               ← 수동 재발송 워크플로 (본인만)
├── local_output/                ← DRY_RUN 미리보기 (git 제외)
└── logs/                        ← 실행 로그 (git 제외)
```

---

## 5. 코딩/수정 원칙

- **기존 동작을 바꾸는 수정은 영향 범위를 먼저 설명하고** 사용자 확인 후 진행한다.
- `weekly.py`와 `benchmark.py`는 **독립**이다. benchmark 작업 시 weekly.py를 import하거나 수정하지 않는다.
- `benchmark.py`는 `history/`를 **읽기 전용**으로만 접근한다. history에 쓰지 않는다.
- 외부 의존성을 함부로 추가하지 않는다. `weekly.py`는 표준 라이브러리 + `anthropic`만 사용한다.
- 한국어 텍스트가 들어가는 파일(특히 `.ps1`)은 인코딩 깨짐에 주의한다 (`run_local.ps1`은 UTF-8 BOM 필요).
- 수정 후에는 반드시 **변경 파일 / 변경 이유 / 검증 방법**을 요약한다.

## 6. 테스트 원칙

- 코드 변경 후 최소한 **문법 체크**(`ast.parse`)는 한다 — API 호출·과금 없음.
- 로직 변경은 가능하면 **함수 단위 단위테스트**를 작은 스크립트로 돌려 확인한다 (API 호출 없는 순수 함수 위주).
- **실제 메일 발송 / 실제 API 수집은 테스트 목적으로 함부로 실행하지 않는다.** 미리보기가 필요하면 `DRY_RUN=1`을 쓴다.
- 벤치마크는 비용이 크므로 첫 실행/검증은 `config/benchmark_config.py`의 값을 낮춰서 작게 돌린다.

## 7. 배포/운영 주의사항

- `weekly.py`는 GitHub Actions cron으로 자동 운영 중이다. 코드를 push하면 **다음 실행부터 즉시 반영**된다 — 검증 없이 push 금지.
- GitHub Actions `weekly.yml`은 실행 후 `history/`를 **자동 커밋·push**한다. history 구조를 바꾸는 변경은 dedup·리포트에 영향을 준다.
- 시크릿(키)은 GitHub 레포 Settings → Secrets에 등록돼 있다. 코드/로그에 평문 노출 금지.

---

## 8. 금지사항 (절대 하면 안 되는 것)

- ❌ `.env` 파일 내용, API 키, 토큰, 비밀번호, 개인정보를 **출력·로그·커밋·문서화**하는 행위.
- ❌ 사용자 확인 없이 **실제 메일 발송 / 실제 배포(push) / 외부 API 호출(과금) / DB·history 변경**.
- ❌ `weekly.py`의 기존 동작(수집·필터·발송·dedup)을 사용자 확인 없이 변경.
- ❌ `benchmark.py`에서 `history/` 파일을 쓰거나 수정.
- ❌ git 강제 명령(`push --force`, `reset --hard`, `clean -f` 등)을 확인 없이 실행.
- ❌ GitHub Actions 워크플로(`weekly.yml`, `resend.yml`)를 확인 없이 변경.

## 9. 사용자 확인 없이 실행하면 안 되는 명령

- `python scripts/weekly.py` (DRY_RUN 아닌 일반/RESEND 모드) — **실제 메일 발송**
- `python scripts/benchmark.py` — **Apify + Claude API 과금**
- `git push`, `git commit`(사용자가 커밋 요청한 경우 제외)
- `gh workflow run ...`, GitHub Actions 수동 트리거
- 그 외 외부 서비스 호출·과금·배포가 발생하는 모든 명령

> **비용 없는 안전 작업** (자유롭게 가능): 파일 읽기, 코드 검색(grep/glob), 문법 체크, 순수 함수 단위테스트, 문서 작성.

---

## 10. 자주 하는 작업 흐름

- **리포트 HTML 디자인만 수정** → `weekly.py`의 `render_*` 함수 또는 `benchmark.py`의 `render_report`/`REPORT_CSS` 수정 → 문법 체크 → (필요 시 `DRY_RUN=1`로 미리보기). 수집·분석 재실행 불필요.
- **필터 기준 변경** → `weekly.py`의 `CONFIG` 또는 `config/benchmark_config.py` 수정 → 영향 범위 설명 → 사용자 확인.
- **벤치마크 참고 채널 추가** → `config/benchmark_config.py`의 `REFERENCE_CHANNELS` 수정.
- **AI 분석 프롬프트 수정** → `weekly.py`의 `ANALYSIS_SYSTEM_PROMPT` 또는 `benchmark.py`의 `BENCHMARK_SYSTEM_PROMPT` 수정.

## 11. 작업 전 체크리스트

1. [ ] `docs/WORK_STATUS.md`로 현재 진행 상황 확인.
2. [ ] 요청이 `weekly.py`(운영 중)에 영향을 주는지 판단.
3. [ ] 요청이 단순 UI/HTML 재생성인지, API 호출·과금이 필요한 작업인지 구분.
4. [ ] 과금/발송/배포가 필요하면 **먼저 사용자에게 확인**.
5. [ ] 수정 시 영향 범위를 설명하고 진행.
6. [ ] 완료 후 변경 파일·이유·검증 방법 요약.

---

## 12. 핵심 원칙 (요약)

1. `.env`·민감정보는 절대 출력/커밋/문서화하지 않는다.
2. **API 비용이 발생하는 실행**과 **단순 UI/HTML 재생성**을 구분한다.
3. 실제 메일 발송·배포·DB 변경·외부 서비스 호출은 사용자 확인 없이 실행하지 않는다.
4. 기존 기능을 변경할 때는 영향 범위를 먼저 설명한다.
5. 수정 후에는 변경 파일·이유·검증 방법을 요약한다.
6. 모르는 내용은 추측해 확정하지 말고 `확인 필요`로 표시한다.
