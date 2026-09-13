# 重启常驻进程：调度服务（含飞书长连接）+ Streamlit 仪表盘
#
# 用法（在【你自己的】PowerShell 里运行）：
#     .\scripts\restart_service.ps1                # 两个都重启
#     .\scripts\restart_service.ps1 -DryRun        # 只列出会停/会启的进程，不动手
#     .\scripts\restart_service.ps1 -SkipDashboard # 只重启调度服务
#     .\scripts\restart_service.ps1 -SkipService   # 只重启仪表盘
#
# 场景：改完**常驻进程内加载**的代码后必须重启才生效——
#   调度服务（run_service.py）：invest/agent/*.py、prompt、tools.py、report.py、feishu_ws.py…
#   仪表盘（run_dashboard.py）：dashboard/*.py
# 走 OS 计划任务的单任务代码（scripts/run_job.py）不用重启，下次触发自动用新代码。
#
# 过程识别（进程命令行读不到时的兜底，避免误杀/重复启动）：
#   服务：job_runs 最近一条 scheduler 的启动时刻 ±180s 对进程创建时间；
#         再不行就看 data/service.lock 是否被占用——占用却识别不出进程时**拒绝启动**（防双实例）。
#   仪表盘：按 8501 端口占用者（Get-NetTCPConnection）兜底；端口被占却识别不出进程时同样拒绝启动。
# 报 Access denied 时：用「管理员身份」打开 PowerShell 再执行本脚本。
param(
    [switch]$DryRun,
    [switch]$SkipService,
    [switch]$SkipDashboard
)
$ErrorActionPreference = "Continue"

$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$root = Split-Path -Parent $here
Set-Location -LiteralPath $root
$py = Join-Path $root "myenv\Scripts\python.exe"
$pyw = Join-Path $root "myenv\Scripts\pythonw.exe"
$svcLog = Join-Path $root "logs\service.log"
$wsLog = Join-Path $root "logs\feishu_ws.log"
$dashOut = Join-Path $root "logs\dashboard.log"
$dashErr = Join-Path $root "logs\dashboard.err.log"
$dashPort = 8501
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

function Get-PyProcs([string]$pattern) {
    # 按命令行匹配；命令行不可读（权限不足）时返回空，由调用方走兜底
    Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match $pattern }
}

function Get-PortOwnerPids([int]$port) {
    try {
        return @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch { return @() }
}

function Get-ProcsByCreationWindow([datetime]$anchor, [int]$seconds) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CreationDate -and
            [math]::Abs((New-TimeSpan -Start $_.CreationDate -End $anchor).TotalSeconds) -le $seconds
        }
}

function Get-LastSchedulerStart {
    # job_runs 最近一条 scheduler 记录 = 服务启动时刻（命令行不可读时用它对进程创建时间）
    try {
        $code = 'import sqlite3;c=sqlite3.connect("file:data/invest.db?mode=ro",uri=True);r=c.execute("select started_at from job_runs where job like ''scheduler'' order by id desc limit 1").fetchone();print(r[0] if r else "")'
        $s = (& $py -c $code 2>$null | Select-Object -First 1)
        if ($s -and $s.Trim() -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
            return [datetime]::ParseExact($matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
        }
    } catch { }
    return $null
}

function Test-ServiceLock {
    # data/service.lock 被占用 = 有调度服务实例活着（run_service 全程持有该文件句柄）
    # 用 .NET 独占打开判断：不依赖子进程，也不受 PS 5.1 吃掉多行 python -c 引号的影响
    try {
        $fs = [System.IO.File]::Open((Join-Path $root 'data\service.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        $fs.Close()
        return $false
    } catch { return $true }
}

function Test-Port([int]$port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2000)
        $c.Close()
        return [bool]$ok
    } catch { return $false }
}

function Stop-Pids([int[]]$pids, [string]$label) {
    foreach ($id in $pids) { Write-Host "  停止 $label pid=$id" }
    foreach ($id in $pids) { Stop-Process -Id $id -Force -ErrorAction Continue }
    Start-Sleep -Seconds 3
    $alive = @()
    foreach ($id in $pids) { if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $alive += $id } }
    return $alive
}

# ---------------------------------------------------------------- 调度服务
if (-not $SkipService) {
    Write-Host "===== [1/2] 调度服务 run_service.py（含飞书长连接）=====" -ForegroundColor Cyan
    Write-Host "[1/4] 查找旧服务进程 ..."
    $old = @(Get-PyProcs 'run_service\.py')
    $how = "命令行匹配"
    if ($old.Count -eq 0) {
        $svcStart = Get-LastSchedulerStart
        if ($svcStart) {
            Write-Host "  命令行不可读，按启动时刻兜底匹配（job_runs: $svcStart）..."
            $old = @(Get-ProcsByCreationWindow $svcStart 180)
            $how = "启动时刻匹配"
        }
    }
    $oldPids = @($old | ForEach-Object { [int]$_.ProcessId })

    if ($DryRun) {
        if ($oldPids.Count -eq 0) {
            Write-Host "  [DRYRUN] 未找到旧服务进程（锁占用=$([bool](Test-ServiceLock))）"
        } else {
            foreach ($p in $old) { Write-Host "  [DRYRUN] 将停止 pid=$($p.ProcessId) started=$($p.CreationDate)" }
        }
        Write-Host "  [DRYRUN] 将启动: $pyw scripts\run_service.py"
    } else {
        if ($oldPids.Count -eq 0) {
            if (Test-ServiceLock) {
                Write-Host "  x 识别不到进程但 data\service.lock 被占用：本脚本权限不足（请用管理员身份重跑）" -ForegroundColor Red
                exit 1
            }
            Write-Host "  未找到旧服务进程（锁空闲，确认没在跑）"
        } else {
            Write-Host "  （识别方式：$how）"
        }
        Write-Host "[2/4] 停止并确认退出 ..."
        $alive = @(Stop-Pids $oldPids '服务')
        if ($alive.Count -gt 0) {
            Write-Host "  x 旧服务仍存活（pid=$($alive -join ',')）：权限不足，请以管理员身份重跑本脚本" -ForegroundColor Red
            exit 1
        }
        Write-Host "  ok 旧进程已退出"

        Write-Host "[3/4] 启动新服务（pythonw 无窗口，与开机自启一致）..."
        Add-Content -LiteralPath $svcLog -Encoding UTF8 -Value "=== manual restart @ $ts ==="
        Start-Process -FilePath $pyw -ArgumentList 'scripts\run_service.py' -WorkingDirectory $root -WindowStyle Hidden
        Start-Sleep -Seconds 20

        Write-Host "[4/4] 校验新实例 ..."
        $new = @(Get-PyProcs 'run_service\.py')
        if ($new.Count -eq 0) {
            Write-Host "  命令行不可读，改用 job_runs 校验（下面若出现新的 scheduler 行即为成功）"
        } elseif ($new.Count -gt 1) {
            Write-Host "  ! 检测到多个 run_service 进程：pid=$($new.ProcessId -join ',')（锁文件只允许一个真正持锁）" -ForegroundColor Yellow
        } else {
            Write-Host "  ok 新服务 pid=$($new.ProcessId -join ',')"
        }
        if (Test-ServiceLock) { Write-Host "  ok 锁已被新实例持有" -ForegroundColor Green }
        & $py "scripts\check_service.py"
        Write-Host "最近飞书长连接日志："
        if (Test-Path -LiteralPath $wsLog) { Get-Content -LiteralPath $wsLog -Tail 3 -Encoding UTF8 }
    }
    Write-Host ""
}

# ---------------------------------------------------------------- 仪表盘
if (-not $SkipDashboard) {
    Write-Host "===== [2/2] 仪表盘 run_dashboard.py（http://localhost:$dashPort）=====" -ForegroundColor Cyan
    Write-Host "[1/4] 查找旧仪表盘进程 ..."
    $dold = @(Get-PyProcs 'run_dashboard\.py|streamlit\s+run.*dashboard[\\/]app\.py')
    $dashPids = @($dold | ForEach-Object { [int]$_.ProcessId })
    $portPids = @(Get-PortOwnerPids $dashPort)
    foreach ($pp in $portPids) { if ($dashPids -notcontains [int]$pp) { $dashPids += [int]$pp } }
    $portBusy = [bool](Test-Port $dashPort)

    if ($DryRun) {
        if ($dashPids.Count -eq 0) { Write-Host "  [DRYRUN] 未找到仪表盘进程（端口 $dashPort 监听=$portBusy）" }
        foreach ($id in $dashPids) { Write-Host "  [DRYRUN] 将停止 pid=$id" }
        Write-Host "  [DRYRUN] 将启动: $py scripts\run_dashboard.py（输出 -> logs\dashboard.log）"
    } else {
        if ($dashPids.Count -eq 0 -and $portBusy) {
            Write-Host "  x 识别不到进程但端口 $dashPort 在监听：本脚本权限不足（请用管理员身份重跑）" -ForegroundColor Red
            exit 1
        }
        if ($dashPids.Count -eq 0) { Write-Host "  未找到仪表盘进程（端口空闲，确认没在跑）" }
        Write-Host "[2/4] 停止并确认退出 ..."
        $dalive = @(Stop-Pids $dashPids '仪表盘')
        if ($dalive.Count -gt 0) {
            Write-Host "  x 旧仪表盘仍存活（pid=$($dalive -join ',')）：权限不足，请以管理员身份重跑本脚本" -ForegroundColor Red
            exit 1
        }
        Write-Host "  ok 旧进程已退出"

        Write-Host "[3/4] 启动新仪表盘（后台，输出写 logs\dashboard.log）..."
        Add-Content -LiteralPath $svcLog -Encoding UTF8 -Value "=== dashboard restart @ $ts ==="
        Start-Process -FilePath $py -ArgumentList 'scripts\run_dashboard.py' -WorkingDirectory $root `
            -WindowStyle Hidden -RedirectStandardOutput $dashOut -RedirectStandardError $dashErr
        Start-Sleep -Seconds 15

        Write-Host "[4/4] 校验进程与端口 ..."
        $dnew = @(Get-PyProcs 'run_dashboard\.py|streamlit\s+run.*dashboard[\\/]app\.py')
        if ($dnew.Count -gt 4) {
            Write-Host "  ! 检测到 $($dnew.Count) 个相关进程（streamlit 正常为 2-4 个父子进程）：pid=$($dnew.ProcessId -join ',')" -ForegroundColor Yellow
        } elseif ($dnew.Count -gt 0) {
            Write-Host "  ok 仪表盘进程 pid=$($dnew.ProcessId -join ',')"
        }
        if (Test-Port $dashPort) {
            Write-Host "  ok 端口 $dashPort 已监听: http://localhost:$dashPort" -ForegroundColor Green
        } else {
            Write-Host "  x 端口 $dashPort 未监听，看 logs\dashboard.err.log" -ForegroundColor Red
        }
        if (Test-Path -LiteralPath $dashErr) {
            $errTail = (Get-Content -LiteralPath $dashErr -Tail 3 -Encoding UTF8) -join " | "
            if ($errTail.Trim()) { Write-Host "  dashboard.err.log 末尾：$errTail" }
        }
    }
    Write-Host ""
}

if ($DryRun) {
    Write-Host "DRYRUN 结束：未做任何改动。" -ForegroundColor Yellow
} else {
    Write-Host "完成：常驻进程已按需重启（调度服务 + 仪表盘）。" -ForegroundColor Green
    Write-Host "  调度服务：飞书对话已用新代码；仪表盘：http://localhost:$dashPort" -ForegroundColor Green
}
