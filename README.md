# 털어드림 주간 후보 자동 메일링

YouTube에서 K-pop Shorts 후보를 매주 금요일 09:00 KST에 자동으로 수집하여
이메일로 발송하고, history 폴더에 영구 아카이브하는 봇.

---

## 동작 방식

1. **매주 금요일 09:00 KST** 에 GitHub Actions가 자동 실행
2. YouTube Data API v3로 22개 검색어 수행
3. 다음 조건 통과한 영상만 후보로 채택:
   - 지난 7일 내 업로드 (부족하면 14일까지 자동 확장)
   - 조회수 100만 이상 500만 미만
   - 한국어 제목
   - Shorts (180초 이하)
   - `@SOONIGROUP` 채널 제외
   - 직캠/챌린지/열애설 키워드 제외
4. 조회수 내림차순 상위 20개를 본인 Gmail로 발송
5. `history/YYYY-MM-DD.json` 파일로 결과 영구 저장 (자동 커밋)

---

## 초기 세팅 (1회, 약 15분)

### 0. 준비물

- [ ] **이 코드 전체** (zip 받았거나 git clone 한 상태)
- [ ] **YouTube Data API 키**
   - https://console.cloud.google.com → 프로젝트 → "API 및 서비스" → "사용자 인증 정보"
   - `+ 사용자 인증 정보 만들기` → API 키
   - 만든 후 키 옆 ⋮ → "API 제한사항" → `YouTube Data API v3` 만 허용
- [ ] **Gmail 앱 비밀번호 (16자리)**
   - https://myaccount.google.com/security
   - 2단계 인증을 먼저 켠 다음
   - "앱 비밀번호" 검색 → 새 비밀번호 → 이름 "털어드림 봇" → 생성
   - 16자리 영숫자가 나옴. **이걸 복사해두세요** (창 닫으면 다시 못 봄)

### 1. GitHub 레포 만들기

1. https://github.com/new
2. Repository name: `teoldeurim-weekly` (아무거나 OK)
3. **Visibility: Private** ← 중요. Public이면 history 후보 리스트가 외부에 노출됨
4. "Create repository"

### 2. 코드 푸시

로컬에서:
```bash
cd 압축푼-폴더
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<본인계정>/teoldeurim-weekly.git
git push -u origin main
```

### 3. 시크릿 4개 등록 ⚠️ **이 단계가 가장 중요**

레포 페이지에서:

1. **Settings** 탭 (오른쪽 상단)
2. 좌측 메뉴 **Secrets and variables** → **Actions**
3. **`New repository secret`** 버튼 클릭, 아래 4개를 각각 등록:

| Name (정확히 이대로) | Secret (값) |
|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API 키 |
| `GMAIL_ADDRESS` | 발송할 Gmail 주소 (예: `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | 위에서 발급받은 앱 비밀번호 16자리 |
| `RECIPIENT_EMAIL` | 받을 메일 주소 (Gmail이 아니어도 됨) |

#### ⚠️ 시크릿 등록 시 함정 (지난번에 걸렸던 그 문제)

다음을 꼭 지키세요:

- **이름은 대소문자·언더스코어까지 정확히 일치** (`Youtube_Api_Key` ❌, `YOUTUBE_API_KEY` ✅)
- **값 끝에 줄바꿈/공백이 들어가지 않게 주의** — 키를 복사할 때 자동으로 `\n`이 따라붙는 경우 흔함. 메모장에 한번 붙여넣어 보고 정말 한 줄인지 확인 후 GitHub에 입력하면 안전
- **앱 비밀번호는 일반 비밀번호 아님** — 일반 비번을 쓰면 SMTP 인증이 즉시 실패함. 반드시 16자리 앱 비밀번호 사용

### 4. 첫 실행 테스트

자동 스케줄을 기다리지 말고 즉시 한 번 돌려서 잘 되는지 확인:

1. 레포 → **Actions** 탭
2. 좌측 **"털어드림 주간 후보 메일링"** 워크플로 선택
3. 우측 **"Run workflow"** 버튼 → 드롭다운 → **"Run workflow"**
4. 실행이 시작되면 클릭해서 실시간 로그 보기
5. 첫 step "**시크릿 사전 검증**"에서 4개 모두 ✅ 떠야 함
   - 만약 ❌가 있으면 그 시크릿을 다시 등록하세요 (이름 오타 또는 빈 값)
6. 약 1~2분 후 본인 메일에 **"[털어드림 주간 후보] ..."** 도착하는지 확인
   - 안 오면 스팸함도 확인
   - 그래도 없으면 Actions 로그 끝까지 보면 에러 메시지 있음

### 5. 자동 스케줄 확인

이미 자동 스케줄은 켜져 있습니다. `.github/workflows/weekly.yml`의

```yaml
on:
  schedule:
    - cron: '0 0 * * 5'   # 매주 금요일 00:00 UTC = 09:00 KST
```

이 부분이 활성화돼 있어서, 다음 금요일 9시(KST) 즈음 자동 실행됩니다.

> ⚠️ **GitHub의 cron 정책**: 60일 동안 레포에 어떤 변경(push/실행)도 없으면 GitHub가 cron을 자동 정지함. 이 봇은 매주 history를 커밋하므로 멈추지 않음.

---

## 운영

### 매주 받는 것

- **본인 메일함**: 표 형태 본문 + 첨부 HTML 리포트
- **레포 history/**: `2026-04-25.json` 같은 파일이 자동 누적

### 설정 바꾸기

`scripts/weekly.py` 상단의 `CONFIG`만 수정하면 됨:

```python
CONFIG = {
    "MIN_VIEWS": 1_000_000,
    "MAX_VIEWS": 5_000_000,
    "MAX_DURATION_SEC": 180,
    "PRIMARY_LOOKBACK_DAYS": 7,
    "CHANNEL_BLOCKLIST": {
        "SOONIGROUP [수니그룹]",
        # 새 채널 추가하려면 여기에 한 줄 추가
    },
    "TITLE_KEYWORD_BLOCKLIST": [
        "직캠", "풀캠", ...
    ],
    "SEARCH_QUERIES": [...],
}
```

수정 후 푸시하면 다음 실행부터 적용됨.

### 발송 시간 바꾸기

`.github/workflows/weekly.yml`의 `cron` 표현식 변경:

| 원하는 시간 (KST) | UTC 환산 | cron 표현식 |
|---|---|---|
| 금요일 09:00 (현재) | 금 00:00 | `'0 0 * * 5'` |
| 금요일 18:00 | 금 09:00 | `'0 9 * * 5'` |
| 토요일 09:00 | 금 24:00 = 토 00:00 | `'0 0 * * 6'` |

### 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `❌ YOUTUBE_API_KEY: 등록 안 됨` | Settings → Secrets에서 이름 정확히 등록 (대소문자 주의) |
| `invalid x-api-key` | API 키 자체가 무효. Google Cloud Console에서 활성/제한 확인 |
| `(535, ... Username and Password not accepted)` | Gmail 앱 비밀번호가 잘못됨. 일반 비번 아니라 16자리 앱 비번이어야 함 |
| `quotaExceeded` | YouTube API 일일 한도 (10,000 유닛) 초과. 다음날까지 대기 |
| 메일은 오는데 후보가 0개 | 그 주에 7일 내 조건 통과 영상이 없었던 것. 일시적 현상. 14일 fallback도 적용됨 |
| 갑자기 멈춤 | 60일 비활성으로 cron 정지된 경우. Actions 탭에서 워크플로 활성화 또는 수동 실행 1회 |

---

## 비용

- **GitHub Actions 사용량**: 회당 약 2분 × 월 4회 = **8분/월** (무료 한도 2,000분)
- **YouTube API 사용량**: 회당 약 2,400 유닛 (일일 한도 10,000)
- **Gmail SMTP**: 무료 (개인 메일 한도 일 500통, 자동 발송이라 충분)
- **총 비용**: 0원

---

## 보안 메모

- 시크릿 4개는 GitHub의 암호화된 저장소에 보관. 레포 코드/로그 어디에도 평문 노출 안 됨
- 앱 비밀번호는 Gmail에서 언제든 폐기 가능 (Google 계정 → 보안 → 앱 비밀번호)
- API 키도 Google Cloud Console에서 폐기/재발급 가능
- ⚠️ **이전 대화에서 노출된 키는 폐기**하고 새 키로 등록하세요
