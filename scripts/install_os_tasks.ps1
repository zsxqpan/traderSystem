# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    把本项目的定时任务注册为 Windows 计划任务（OS 级调度，替代 APScheduler 常驻）。
    用 schtasks /Create /XML 注册（避免 CLI 引号转义问题），并开启 StartWhenAvailable
    （错过时间点后补跑）+ IgnoreNew（不重叠）。

    注册的任务（盘中 10s 轮询除外——OS 任务无法低于 1 分钟粒度，由 run_service --ticker-only 常驻承载）：
        TraderSystem_premarket          交易日 08:30
        TraderSystem_morning_brief      交易日 08:40
        TraderSystem_auction            交易日 09:26
        TraderSystem_snapshot_close     交易日 15:01
        TraderSystem_after_close        交易日 16:00
        TraderSystem_pool_trap_scan     交易日 16:20
        TraderSystem_industry_refresh   交易日 16:30
        TraderSystem_daily_refresh      交易日 16:40
        TraderSystem_factcard_refresh   交易日 16:50
        TraderSystem_evening_report     交易日 17:00
        TraderSystem_weekend            周日   20:00
        TraderSystem_monthly            每月1日 09:30
        TraderSystem_yearly             每年1/1 09:30
        TraderSystem_action_digest_am   交易日 10:00
        TraderSystem_action_digest_pm   交易日 13:30
        TraderSystem_big_v_harvest      每天   17:10
    电源策略任务（2026-09-07：机器仅 08:30-17:30 唤醒，其余时间允许休眠省电）：
        TraderSystem_power_on           交易日 08:25（唤醒机器 + 插电永不休眠，需管理员）
        TraderSystem_power_off          交易日 17:35（恢复插电 25 分钟休眠，需管理员）

    用法（在【你自己的】PowerShell 里运行）：
        powershell -ExecutionPolicy Bypass -File "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem\scripts\install_os_tasks.ps1"
    卸载：
        powershell -ExecutionPolicy Bypass -File "...\install_os_tasks.ps1" -Uninstall
    迁移后重启常驻服务（默认就是 ticker-only，无需任何参数）：
        powershell -ExecutionPolicy Bypass -File "...\scripts\restart_service.ps1"
#>
param([switch]$Uninstall, [switch]$DryRun)

$ErrorActionPreference = "Stop"
$root = "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem"
$py   = Join-Path $root "myenv\Scripts\python.exe"
$tmpDir = Join-Path $root ".tmp"

# 任务名 -> @{ Desc, Trigger(weekday/sunday/daily/monthly/yearly), Time, Job }
$jobs = @(
    @{ Name = "TraderSystem_premarket";        Desc = "盘前清单+采集+quant 08:30";         Trigger = 'weekday'; Time = "08:30"; Job = "premarket" },
    @{ Name = "TraderSystem_morning_brief";    Desc = "盘前信息早报 08:40";                Trigger = 'weekday'; Time = "08:40"; Job = "morning_brief" },
    @{ Name = "TraderSystem_auction";          Desc = "集合竞价报告 09:26";                Trigger = 'weekday'; Time = "09:26"; Job = "auction" },
    @{ Name = "TraderSystem_snapshot_close";   Desc = "收盘即日线快照 15:01";             Trigger = 'weekday'; Time = "15:01"; Job = "snapshot_close" },
    @{ Name = "TraderSystem_after_close";      Desc = "盘后采集/Agent/扫描/快照 16:00";    Trigger = 'weekday'; Time = "16:00"; Job = "after_close" },
    @{ Name = "TraderSystem_pool_trap_scan";   Desc = "候选池杀猪盘扫描 16:20";            Trigger = 'weekday'; Time = "16:20"; Job = "pool_trap_scan" },
    @{ Name = "TraderSystem_weekend";          Desc = "周日20:00 周报(大模型消息面+复盘)"; Trigger = 'sunday';  Time = "20:00"; Job = "weekend" },
    @{ Name = "TraderSystem_monthly";          Desc = "每月1日 月度复盘";                  Trigger = 'monthly'; Time = "09:30"; Job = "monthly" },
    @{ Name = "TraderSystem_yearly";           Desc = "每年1/1 年度复盘";                  Trigger = 'yearly';  Time = "09:30"; Job = "yearly" },
    @{ Name = "TraderSystem_industry_refresh"; Desc = "16:30 行业数据刷新";                Trigger = 'weekday'; Time = "16:30"; Job = "industry_refresh" },
    @{ Name = "TraderSystem_daily_refresh";    Desc = "16:40 日线/指数补采+quant";         Trigger = 'weekday'; Time = "16:40"; Job = "daily_refresh" },
    @{ Name = "TraderSystem_factcard_refresh"; Desc = "16:50 行业事实卡/重要变化推送";       Trigger = 'weekday'; Time = "16:50"; Job = "factcard_refresh" },
    @{ Name = "TraderSystem_evening_report";   Desc = "17:00 晚间盘后报告(含数据滞后门禁)"; Trigger = 'weekday'; Time = "17:00"; Job = "evening_report" },
    @{ Name = "TraderSystem_action_digest_am"; Desc = "10:00 动作 digest";                 Trigger = 'weekday'; Time = "10:00"; Job = "action_digest" },
    @{ Name = "TraderSystem_action_digest_pm"; Desc = "13:30 动作 digest";                 Trigger = 'weekday'; Time = "13:30"; Job = "action_digest_pm" },
    @{ Name = "TraderSystem_big_v_harvest";    Desc = "17:10 大V画像库雪球慢速回灌";         Trigger = 'daily'; Time = "17:10"; Job = "big_v_harvest" }
)

function New-TriggerXml([string]$kind, [string]$time) {
    $boundary = "2026-08-18T$time" + ":00"
    switch ($kind) {
        "weekday" {
            "<CalendarTrigger><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled>" +
            "<ScheduleByWeek><DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek>" +
            "<WeeksInterval>1</WeeksInterval></ScheduleByWeek></CalendarTrigger>"
        }
        "sunday" {
            "<CalendarTrigger><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled>" +
            "<ScheduleByWeek><DaysOfWeek><Sunday/></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek></CalendarTrigger>"
        }
        "daily" {
            "<CalendarTrigger><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled>" +
            "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
        }
        "monthly" {
            "<CalendarTrigger><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled>" +
            "<ScheduleByMonth><DaysOfMonth><Day>1</Day></DaysOfMonth><Months>" +
            "<January/><February/><March/><April/><May/><June/><July/><August/><September/><October/><November/><December/>" +
            "</Months></ScheduleByMonth></CalendarTrigger>"
        }
        "yearly" {
            "<CalendarTrigger><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled>" +
            "<ScheduleByMonth><DaysOfMonth><Day>1</Day></DaysOfMonth><Months><January/></Months>" +
            "</ScheduleByMonth></CalendarTrigger>"
        }
        default { throw "unknown trigger kind: $kind" }
    }
}

function New-TaskXml([string]$name, [string]$desc, [string]$triggerXml, [string]$job) {
    $log = (Join-Path $root ("logs\" + $job + ".log"))
    $rootEsc = $root
    $pyEsc = $py
    # XML 转义（& < > 在 Arguments 里必须转义）
    $cmdArgs = "/c cd /d `"$rootEsc`" &amp;&amp; `"$pyEsc`" -u scripts\run_job.py $job &gt;&gt; `"$log`" 2&gt;&amp;1"
    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$desc</Description></RegistrationInfo>
  <Triggers>$triggerXml</Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>$cmdArgs</Arguments>
      <WorkingDirectory>$rootEsc</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

function New-PowerTaskXml([string]$name, [string]$desc, [string]$time, [string]$execLimit, [string]$command, [string]$arguments, [string]$workingDir) {
    # 电源策略任务：工作日到点唤醒机器。
    # - power_on：启动 keep_awake.py（pythonw，持有 ES_SYSTEM_REQUIRED 至 17:30，
    #   阻止"唤醒任务结束后自动复睡"——08-26:56 复睡导致 premarket 漏跑事故，2026-09-08 改）
    # - power_off：恢复插电 25 分钟休眠。
    # 需以管理员注册（RunLevel=HighestAvailable）+ WakeToRun 唤醒睡眠中的机器。
    # 2026-09-14 事故：原先统一用 cmd.exe /c "…pythonw.exe" "…keep_awake.py" --until 17:30，
    # 四个引号触发 cmd 的剥引号规则（/c 后首字符是引号且引号数≠2 → 删掉最后一个引号），
    # 命令行被解析坏、返回码 1 → keep_awake 自 09-08 起从未启动，机器白天自动睡眠、
    # 漏掉 16:20-17:10 整条盘后链。现在直接 Exec 目标程序，不经 cmd 包装。
    $boundary = "2026-09-07T$time" + ":00"
    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$desc</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$boundary</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek><DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>$execLimit</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$command</Command>
      <Arguments>$arguments</Arguments>
      <WorkingDirectory>$workingDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

$pyw = Join-Path $root "myenv\Scripts\pythonw.exe"
$keepAwake = Join-Path $root "scripts\keep_awake.py"
$powercfg = Join-Path $env:SystemRoot "System32\powercfg.exe"
$powerJobs = @(
    @{ Name = "TraderSystem_power_on";  Desc = "工作日08:25 唤醒+keep_awake保活至17:30"; Time = "08:25"; ExecLimit = "PT10H";
       Command = $pyw; Arguments = "`"$keepAwake`" --until 17:30"; WorkingDir = $root },
    @{ Name = "TraderSystem_power_off"; Desc = "工作日17:35 恢复插电25分钟休眠";          Time = "17:35"; ExecLimit = "PT10M";
       Command = $powercfg; Arguments = "/change standby-timeout-ac 25"; WorkingDir = $root }
)

if ($Uninstall) {
    foreach ($j in $jobs) {
        & schtasks /Delete /TN $j.Name /F 2>$null | Out-Null
        Write-Host "已删除: $($j.Name)"
    }
    foreach ($p in $powerJobs) {
        & schtasks /Delete /TN $p.Name /F 2>$null | Out-Null
        Write-Host "已删除: $($p.Name)"
    }
    exit 0
}

New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
Write-Host "=== 注册计划任务（目标: $root）==="
foreach ($j in $jobs) {
    $trigger = New-TriggerXml $j.Trigger $j.Time
    $xml = New-TaskXml $j.Name $j.Desc $trigger $j.Job
    $xmlFile = Join-Path $tmpDir ("task_" + $j.Job + ".xml")
    [System.IO.File]::WriteAllText($xmlFile, $xml, ([System.Text.Encoding]::Unicode))
    if ($DryRun) {
        Write-Host ("[DRYRUN] {0}  ->  {1}" -f $j.Name, $xmlFile)
        continue
    }
    & schtasks /Create /TN $j.Name /XML $xmlFile /F | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("[OK] {0}  {1}" -f $j.Name, $j.Desc)
    } else {
        Write-Host ("[FAIL] {0} (exit={1})" -f $j.Name, $LASTEXITCODE)
    }
}
Write-Host ""
Write-Host "=== 电源策略任务（08:25 唤醒+keep_awake 保活 / 17:35 恢复休眠；需管理员注册）==="
foreach ($p in $powerJobs) {
    $xml = New-PowerTaskXml $p.Name $p.Desc $p.Time $p.ExecLimit $p.Command $p.Arguments $p.WorkingDir
    $xmlFile = Join-Path $tmpDir ("task_" + $p.Name + ".xml")
    [System.IO.File]::WriteAllText($xmlFile, $xml, ([System.Text.Encoding]::Unicode))
    if ($DryRun) {
        Write-Host ("[DRYRUN] {0}  ->  {1}" -f $p.Name, $xmlFile)
        continue
    }
    & schtasks /Create /TN $p.Name /XML $xmlFile /F | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("[OK] {0}  {1}" -f $p.Name, $p.Desc)
    } else {
        Write-Host ("[FAIL] {0} (exit={1})，请用管理员 PowerShell 运行本脚本" -f $p.Name, $LASTEXITCODE)
    }
}
Write-Host ""
Write-Host "下一步：重启常驻服务（run_service.py 默认就是 ticker-only，无需 --ticker-only；"
Write-Host "        10s 轮询 + 每分钟补偿 + 飞书长连接仍需常驻）："
Write-Host "    powershell -ExecutionPolicy Bypass -File `"$root\scripts\restart_service.ps1`""
Write-Host "验证：schtasks /Query /TN TraderSystem_evening_report"
Write-Host "     schtasks /Query /TN TraderSystem_power_on /v /fo LIST   (WakeToRun 应为 True)"
