$ErrorActionPreference = "Stop"
$root = "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem"
Set-Location -LiteralPath $root
$py = Join-Path $root "myenv\Scripts\python.exe"
$log = Join-Path $root "logs\service.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# 互斥保护:已有 run_service.py 进程在跑则直接退出(避免双实例)
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "run_service\.py" }
if ($existing) {
    Add-Content -LiteralPath $log -Encoding UTF8 -Value "=== skip @ $ts (already running pid=$($existing.ProcessId -join ',') ) ==="
    exit 0
}

Add-Content -LiteralPath $log -Encoding UTF8 -Value "=== service start via Task Scheduler @ $ts ==="
& $py "scripts\run_service.py" *>> $log