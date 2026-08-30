# scripts/refresh_cache.ps1을 Windows 작업 스케줄러에 등록한다 (1회성 설정 스크립트).
# 매일 지정 시각에 실행되도록 등록하지만, refresh_cache.ps1 자체가 "오늘이 이번 달
# 마지막 날인지" 판단해서 아니면 즉시 종료하므로 실질적으로는 월 1회만 동작한다.
#
# 사용법 (관리자 권한 필요 없음, 현재 로그온 사용자 계정으로 등록):
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1

$TaskName = "KMarketScreener-MonthlyCacheRefresh"
$ScriptPath = Join-Path $PSScriptRoot "refresh_cache.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$Trigger = New-ScheduledTaskTrigger -Daily -At 7:00AM

$Settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -DontStopOnIdleEnd

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "매일 07:00에 확인해서 이번 달 마지막 날이면 K-Market 스크리너 DART 캐시를 갱신하고 git push" `
    -Force

Write-Output "등록 완료: $TaskName"
