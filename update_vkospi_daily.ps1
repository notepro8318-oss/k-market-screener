# Windows 작업 스케줄러가 매일 실행하는 VKOSPI 자동 갱신 스크립트.
# crawl_vkospi.py 실행 -> 성공하고 값이 바뀌었으면 data/vkospi_cache.json을 커밋/푸시.
# 실패하거나 값이 그대로면 커밋하지 않는다 (기존 캐시 유지, market_dashboard.py의
# 5일 초과 시 VIX 자동 대체 로직이 있어 하루이틀 실패해도 대시보드는 죽지 않는다).

$ErrorActionPreference = "Stop"
Set-Location "D:\ai code\k-market scrinner"
$python = "C:\Users\notep\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$logFile = "vkospi_cron.log"

function Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - $msg" | Out-File -FilePath $logFile -Append -Encoding utf8
}

Log "VKOSPI 갱신 시작"

try {
    & $python crawl_vkospi.py 2>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
    if ($LASTEXITCODE -eq 0) {
        git add data/vkospi_cache.json
        $diff = git diff --cached --stat
        if ($diff) {
            git commit -m "Update VKOSPI cache ($(Get-Date -Format 'yyyy-MM-dd'))" | Out-File -FilePath $logFile -Append -Encoding utf8
            git push origin main | Out-File -FilePath $logFile -Append -Encoding utf8
            Log "커밋/푸시 완료"
        } else {
            Log "값 변동 없음 - 커밋 생략"
        }
    } else {
        Log "크롤링 실패(exit $LASTEXITCODE) - 커밋 생략, 기존 캐시 유지"
    }
} catch {
    Log "오류: $_"
}
