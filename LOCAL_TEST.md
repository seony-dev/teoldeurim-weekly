# 로컬에서 한 번 돌려보기 (선택)

GitHub Actions에 올리기 전에 본인 컴퓨터에서 먼저 검증하고 싶다면:

## 1. Python 3.11 이상 설치 확인
```bash
python3 --version
```

## 2. 환경변수 4개 설정

### macOS / Linux
```bash
export YOUTUBE_API_KEY="발급받은-키"
export GMAIL_ADDRESS="you@gmail.com"
export GMAIL_APP_PASSWORD="abcdefghijklmnop"   # 16자리 앱 비밀번호
export RECIPIENT_EMAIL="받을주소@example.com"
```

### Windows (PowerShell)
```powershell
$env:YOUTUBE_API_KEY="발급받은-키"
$env:GMAIL_ADDRESS="you@gmail.com"
$env:GMAIL_APP_PASSWORD="abcdefghijklmnop"
$env:RECIPIENT_EMAIL="받을주소@example.com"
```

## 3. 실행
```bash
python3 scripts/weekly.py
```

성공하면:
- `history/YYYY-MM-DD.json` 파일이 생성되고
- 메일이 `RECIPIENT_EMAIL`로 발송됩니다

실패하면 콘솔에 에러 메시지가 나옵니다. 자주 보는 에러:

| 메시지 | 원인 |
|---|---|
| `필수 환경변수 누락` | export/set 다시 확인 |
| `403 quotaExceeded` | YouTube API 일일 한도 초과 (다음 날 재시도) |
| `(535, ... Username and Password not accepted)` | Gmail 앱 비번 잘못됨 |
| `socket.gaierror` | 인터넷 연결 또는 DNS 문제 |

## 4. history 파일 정리

로컬 테스트로 만든 history JSON은 커밋 전에 삭제하세요:
```bash
rm history/2026-*.json
```

(GitHub Actions 첫 실행 시 자동으로 다시 생성됩니다)
