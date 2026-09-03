# Windows 작업 스케줄러가 매일 실행하는 시장 대시보드 보조지표 자동 갱신 스크립트.
# 1) crawl_vkospi.py    (investing.com, VKOSPI)
# 2) crawl_kospi_pbr.py (indexergo.com, 코스피 PBR)
# 각각 성공하고 값이 바뀐 캐시 파일만 모아서 한 번에 커밋/푸시한다.
# 실패하거나 값이 그대로면 커밋하지 않는다 (기존 캐시 유지 - market_dashboard.py의
# 5일 초과 시 자동 대체/직접입력 요구 로직이 있어 하루이틀 실패해도 대시보드는 죽지 않는다).

$ErrorActionPreference = "Stop"
Set-Location "D:\ai code\k-market scrinner"
$python = "C:\Users\notep\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$logFile = "market_data_cron.log"

function Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - $msg" | Out-File -FilePath $logFile -Append -Encoding utf8
}

Log "시장 지표 갱신 시작"

try {
    & $python crawl_vkospi.py 2>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
    & $python crawl_kospi_pbr.py 2>&1 | Out-File -FilePath $logFile -Append -Encoding utf8

    git add data/vkospi_cache.json data/kospi_pbr_cache.json
    $diff = git diff --cached --stat
    if ($diff) {
        git commit -m "Update market dashboard cache ($(Get-Date -Format 'yyyy-MM-dd'))" | Out-File -FilePath $logFile -Append -Encoding utf8
        git push origin main | Out-File -FilePath $logFile -Append -Encoding utf8
        Log "커밋/푸시 완료"
    } else {
        Log "값 변동 없음 - 커밋 생략"
    }
} catch {
    Log "오류: $_"
}
