# 매월 마지막 날에만 DART 스크리닝 캐시를 갱신하고 git에 커밋/푸시한다.
#
# OpenDART는 해외 IP(GitHub Actions 러너 포함)를 방화벽에서 차단하므로 이 작업은
# 반드시 한국 IP를 가진 이 PC에서 실행되어야 한다. Windows 작업 스케줄러가 이 스크립트를
# 매일 실행하도록 등록해두고, 스크립트 스스로 "오늘이 이번 달 마지막 날인지" 판단해서
# 아니면 즉시 종료한다 (작업 스케줄러 자체의 "매월 마지막 날" 트리거는 설정이 번거로워
# 이 방식이 더 단순하고 확인하기 쉽다).
#
# 필요 조건: DART_API_KEY가 사용자 환경 변수로 설정되어 있어야 한다.
#   setx DART_API_KEY "발급받은_키"

$ErrorActionPreference = "Stop"
$RepoRoot = "D:\ai code\k-market scrinner"
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("cache_refresh_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))

function Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Output $line
    Add-Content -Path $LogFile -Value $line
}

Set-Location $RepoRoot

$today = Get-Date
if ($today.AddDays(1).Month -eq $today.Month) {
    Log "오늘은 이번 달 마지막 날이 아님 - 종료"
    exit 0
}

if (-not $env:DART_API_KEY) {
    Log "DART_API_KEY 환경 변수가 설정되어 있지 않음 - 종료 (setx DART_API_KEY 로 설정 필요)"
    exit 1
}

Log "월말 캐시 갱신 시작"

python batch.py 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Null
if ($LASTEXITCODE -ne 0) {
    Log "batch.py 실행 실패 (exit $LASTEXITCODE) - 커밋하지 않음"
    exit 1
}

$rowCount = (Import-Csv "data\screening_cache.csv").Count
if ($rowCount -lt 100) {
    Log "캐시 결과가 비정상적으로 적음 ($rowCount 종목) - 데이터 이상으로 판단, 커밋하지 않음"
    exit 1
}

Log "캐시 $rowCount 종목 생성 확인 - git 커밋/푸시 진행"

git add data/screening_cache.csv data/screening_cache_meta.json 2>&1 | Add-Content -Path $LogFile

$hasChanges = git status --porcelain data/screening_cache.csv data/screening_cache_meta.json
if (-not $hasChanges) {
    Log "이전 캐시와 동일 (변경 없음) - 커밋 생략"
    exit 0
}

git commit -m "Automated monthly cache refresh ($(Get-Date -Format yyyy-MM-dd))" 2>&1 | Add-Content -Path $LogFile
git push origin main 2>&1 | Add-Content -Path $LogFile

Log "완료"
