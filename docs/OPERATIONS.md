# 운영 / 실행 가이드 (OPERATIONS)

이 프로젝트의 실행 방법, 모드별 동작, 비용, 위험 작업 주의사항을 정리한 문서입니다.

---

## 1. 로컬 실행 방법

### 주간 메일러 (weekly.py)

```powershell
# Windows — 권장. .env를 읽어 환경변수 주입 후 실행
./run_local.ps1
```
```bash
# macOS / Linux — 환경변수를 직접 export 후 실행
export YOUTUBE_API_KEY="..."   # 등 5개 (.env.example 참고)
python scripts/weekly.py
```

`run_local.ps1` 동작: `.env` 로드 → `requirements.txt` 의존성 설치 → `weekly.py` 실행.
(이 스크립트는 한글 깨짐 방지를 위해 UTF-8 BOM으로 저장돼 있어야 함.)

### 벤치마크 모듈 (benchmark.py)

```bash
python scripts/benchmark.py
```
`benchmark.py`는 `.env`를 자체적으로 읽습니다. `APIFY_TOKEN`, `ANTHROPIC_API_KEY` 필요.

---

## 2. weekly.py 실행 모드

환경변수로 모드를 제어합니다 (`.env` 또는 셸 환경변수에 `1` 지정).

| 모드 | 트리거 | 동작 | 메일 발송 | API 과금 |
|---|---|---|---|---|
| **일반** | (기본) | 수집 → 분석 → 발송 → history 저장 | ✅ | ✅ |
| **DRY_RUN** | `DRY_RUN=1` | 수집·분석은 하되 메일·history 변경 안 함. `local_output/`에 미리보기 HTML 저장 | ❌ | ✅ |
| **FORCE_RESCAN** | `FORCE_RESCAN=1` | 오늘자 history를 무시하고 새로 수집·분석 | ✅ | ✅ |
| **RESEND_LATEST** | `RESEND_LATEST=1` | 최신 history를 수집·분석 없이 메일만 재발송 | ✅ | ❌ |
| **RESUME** | (자동) | 오늘자 history가 이미 있으면 수집·분석 스킵하고 메일만 재발송 | ✅ | ❌ |

> DRY_RUN은 같은 날 `local_output/`에 캐시(`report_data_*.json`)가 있으면 파이프라인을
> 건너뛰고 HTML만 재생성합니다. 새로 수집하려면 `FORCE_RESCAN=1`을 함께 지정.

---

## 3. 테스트 실행 방법

| 목적 | 방법 |
|---|---|
| 코드 문법만 검증 | `python -c "import ast; ast.parse(open('scripts/weekly.py',encoding='utf-8').read())"` |
| 메일 없이 리포트 미리보기 | `DRY_RUN=1` 설정 후 `weekly.py` 실행 → `local_output/preview_*.html` 확인 |
| 벤치마크 소규모 테스트 | `config/benchmark_config.py` 값을 낮춰 실행 (아래 참고) |
| 순수 함수 단위테스트 | API 호출 없는 함수를 작은 스크립트로 import해 검증 |

### 벤치마크 소규모 테스트 설정
`config/benchmark_config.py`에서 다음 값을 낮추면 비용·시간이 줄어듭니다
(현재 파일에는 `[테스트값]` 주석으로 표시된 임시값이 들어가 있을 수 있음):
- `MAX_SHORTS_PER_CHANNEL` (채널당 수집 수)
- `MAX_ANALYSIS_CANDIDATES` (Claude 분석 수 = 비용의 핵심 변수)
- `FINAL_CANDIDATES` (리포트 노출 수)
- `MIN_VIEWS` (조회수 하한)

---

## 4. 배포 / 운영 흐름

별도 서버 없음 — **GitHub Actions cron** 운영.

### weekly.yml (주간 자동 발송)
- 트리거: `cron: '0 0 * * 5'` (금 00:00 UTC = 09:00 KST) + 수동 실행(`workflow_dispatch`)
- 흐름: 체크아웃 → Python 설치 → 의존성 설치 → 시크릿 검증 → `weekly.py` 실행 →
  `history/` 변경분 자동 커밋·push
- 수동 실행 시 `force_rescan` 입력 옵션 제공

### resend.yml (수동 재발송, 본인만)
- 트리거: `workflow_dispatch` 전용
- `RESEND_LATEST=1` + `RECIPIENT_EMAIL_SELF`로 최신 history를 **본인에게만** 재발송
- 수집·분석·history 변경 없음

### benchmark.yml
- **아직 미작성.** 벤치마크는 현재 로컬 수동 실행만 가능.
- 작성 시 weekly와 다른 주기(월 1~2회 권장)·별도 `concurrency` 그룹 권장.

> 코드를 `main`에 push하면 다음 cron 실행부터 즉시 반영됩니다. 검증 없이 push 금지.

---

## 5. 재실행 / 재생성 스크립트

| 스크립트 / 모드 | 역할 |
|---|---|
| `run_local.ps1` | `.env` 로드 후 `weekly.py` 로컬 실행 |
| `RESEND_LATEST=1` | 최신 history 메일만 재발송 (재수집 없음) |
| `DRY_RUN=1` | 미리보기 HTML만 재생성 (`local_output/`) |
| GitHub `resend.yml` | GitHub에서 본인에게만 재발송 |

---

## 6. 비용이 발생하는 명령 vs 발생하지 않는 명령

### 💸 비용/위험 발생 — 사용자 확인 필수
- `python scripts/weekly.py` (일반/RESEND/RESUME) — **실제 메일 발송**, YouTube/Claude API 호출
- `python scripts/benchmark.py` — **Apify(유료) + Claude API 과금**
- GitHub Actions 수동 실행(`weekly.yml`, `resend.yml`) — 메일 발송
- `git push` — 운영 코드 즉시 반영
- `DRY_RUN=1` 실행 — 메일은 안 가지만 **YouTube/Claude API는 호출됨** (과금 O)

### ✅ 비용 없음 — 자유롭게 가능
- 파일 읽기 / 코드 검색(grep, glob)
- 문법 체크 (`ast.parse`)
- API 호출 없는 순수 함수 단위테스트
- 문서 작성 / `git status`, `git diff`, `git log` 등 조회

---

## 7. 위험 작업 주의사항

- **메일 발송**: weekly.py 일반 실행은 즉시 실제 수신자에게 메일을 보냄. 테스트는 `DRY_RUN=1`.
- **외부 API 호출**: YouTube는 일일 쿼터(10,000 유닛), Apify·Claude는 과금. 반복 실행 자제.
- **GitHub Actions push**: weekly.yml이 `history/`를 자동 커밋함. history 구조 변경은
  dedup·리포트에 영향 → 신중히.
- **benchmark.py와 history**: benchmark는 history를 **읽기만** 함. 절대 history에 쓰지 말 것.
- **시크릿**: 키·토큰·비밀번호를 로그·코드·문서에 출력 금지.

---

## 8. 문제 발생 시 점검 순서

1. **환경변수** — `.env` 또는 GitHub 시크릿의 변수명·값·공백 확인
2. **로그 확인** — 로컬은 `logs/`, GitHub은 Actions 탭 실행 로그
3. **에러 메시지 분류**
   - `quotaExceeded` → YouTube API 일일 한도 초과 → 다음날 재시도
   - `535 ... Username and Password not accepted` → Gmail 앱 비밀번호 오류
   - `invalid x-api-key` → YouTube API 키 무효
   - Apify 관련 오류 → `APIFY_TOKEN` 및 Apify 콘솔 잔액·실행 상태 확인
4. **history 상태** — 오늘자 history 존재 여부에 따라 RESUME 모드로 빠질 수 있음
5. **재현** — 가능하면 `DRY_RUN=1`로 메일 없이 재현해 원인 격리
6. 그래도 막히면 `docs/HANDOFF.md`의 리스크 항목과 `README.md` 트러블슈팅 표 참고
