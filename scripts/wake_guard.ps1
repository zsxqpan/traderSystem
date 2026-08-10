# Wake guard: runs via Task Scheduler (WakeToRun) before 08:30 / 16:00 / 22:00 pushes.
# 1) Start the scheduler service if it is not running.
# 2) Watchdog: if job_runs has no new entries for > 8h, the scheduler loop is frozen
#    (machine slept through wake timers, observed 2026-08-09/10) - restart it.
Start-Sleep -Seconds 8
$root = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $root 'myenv\Scripts\pythonw.exe'
$svcPy = Join-Path $root 'scripts\run_service.py'
$svc = Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*traderSystem*' }
if (-not $svc) {
    Start-Process -FilePath $pyw -ArgumentList $svcPy -WorkingDirectory $root -WindowStyle Hidden
} else {
    $db = Join-Path $root 'data\invest.db'
    $py = Join-Path $root 'myenv\Scripts\python.exe'
    $probe = "import sqlite3,datetime,sys; c=sqlite3.connect(r'$db'); r=c.execute('SELECT MAX(started_at) FROM job_runs').fetchone()[0]; c.close(); sys.exit(1 if (not r or (datetime.datetime.now()-datetime.datetime.strptime(r,'%Y-%m-%d %H:%M:%S')).total_seconds()>28800) else 0)"
    & $py -c $probe 2>$null
    if ($LASTEXITCODE -eq 1) {
        Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*traderSystem*' } | Stop-Process -Force
        Start-Sleep -Seconds 3
        Start-Process -FilePath $pyw -ArgumentList $svcPy -WorkingDirectory $root -WindowStyle Hidden
    }
}