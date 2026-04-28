# 로컬 테스트용: .env 파일을 읽어 환경변수로 주입한 뒤 weekly.py 실행
# 사용법: PowerShell에서 ./run_local.ps1
# (이 파일은 UTF-8 BOM으로 저장돼 있어야 PowerShell 5.1에서 한글이 안 깨짐)

# Windows PowerShell 한글 깨짐 방지 — 콘솔/Python 모두 UTF-8 강제
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "[X] .env 파일이 없습니다: $envFile" -ForegroundColor Red
    exit 1
}

Get-Content $envFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $parts = $line -split "=", 2
        if ($parts.Count -eq 2) {
            $name = $parts[0].Trim()
            $value = $parts[1].Trim()
            Set-Item -Path "env:$name" -Value $value
        }
    }
}

Write-Host "[OK] .env 로드 완료." -ForegroundColor Green

# 의존성 체크 (anthropic SDK)
$reqFile = Join-Path $PSScriptRoot "requirements.txt"
if (Test-Path $reqFile) {
    Write-Host "[..] 의존성 확인 중..." -ForegroundColor Cyan
    python -m pip install -q -r $reqFile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] 의존성 설치 실패" -ForegroundColor Red
        exit 1
    }
}

Write-Host "[GO] weekly.py 실행..." -ForegroundColor Green
python scripts/weekly.py
