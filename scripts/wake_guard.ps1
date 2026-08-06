# Wake guard: runs at 21:58 via Task Scheduler (WakeToRun).
# Ensures the scheduler service is alive so the 22:00 nightly push fires.
Start-Sleep -Seconds 8
$root = Split-Path -Parent $PSScriptRoot
$svc = Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*traderSystem*' }
if (-not $svc) {
    Start-Process -FilePath (Join-Path $root 'myenv\Scripts\pythonw.exe') `
                 -ArgumentList (Join-Path $root 'scripts\run_service.py') `
                 -WorkingDirectory $root -WindowStyle Hidden
}