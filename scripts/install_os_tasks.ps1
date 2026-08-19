# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    把本项目的定时任务注册为 Windows 计划任务（OS 级调度，替代 APScheduler 常驻）。
    用 schtasks /Create /XML 注册（避免 CLI 引号转义问题），并开启 StartWhenAvailable
    （错过时间点后补跑）+ IgnoreNew（不重叠）。

    注册的任务（盘中 10s 轮询除外——OS 任务无法低于 1 分钟粒度，由 run_service --ticker-only 常驻承载）：
        TraderSystem_premarket          交易日 08:30
        TraderSystem_morning_brief      交易日 08:40
        TraderSystem_after_close        交易日 16:00
        TraderSystem_weekend            周日   20:00
        TraderSystem_monthly            每月1日 09:30
        TraderSystem_yearly             每年1/1 09:30
        TraderSystem_industry_refresh   交易日 21:30
        TraderSystem_daily_refresh      交易日 21:40
        TraderSystem_evening_report     每日   22:00

    用法（在【你自己的】PowerShell 里运行）：
        powershell -ExecutionPolicy Bypass -File "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem\scripts\install_os_tasks.ps1"
    卸载：
        powershell -ExecutionPolicy Bypass -File "...\install_os_tasks.ps1" -Uninstall
    迁移后把常驻服务改为仅 ticker：
        myenv\Scripts\python.exe -u scripts\run_service.py --ticker-only
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
    @{ Name = "TraderSystem_after_close";      Desc = "盘后采集/Agent/扫描/快照 16:00";    Trigger = 'weekday'; Time = "16:00"; Job = "after_close" },
    @{ Name = "TraderSystem_weekend";          Desc = "周日20:00 周报(大模型消息面+复盘)"; Trigger = 'sunday';  Time = "20:00"; Job = "weekend" },
    @{ Name = "TraderSystem_monthly";          Desc = "每月1日 月度复盘";                  Trigger = 'monthly'; Time = "09:30"; Job = "monthly" },
    @{ Name = "TraderSystem_yearly";           Desc = "每年1/1 年度复盘";                  Trigger = 'yearly';  Time = "09:30"; Job = "yearly" },
    @{ Name = "TraderSystem_industry_refresh"; Desc = "21:30 行业数据刷新";                Trigger = 'weekday'; Time = "21:30"; Job = "industry_refresh" },
    @{ Name = "TraderSystem_daily_refresh";    Desc = "21:40 日线/指数补采+quant";         Trigger = 'weekday'; Time = "21:40"; Job = "daily_refresh" },
    @{ Name = "TraderSystem_evening_report";   Desc = "22:00 晚间盘后报告(含数据滞后门禁)"; Trigger = 'daily';   Time = "22:00"; Job = "evening_report" }
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

if ($Uninstall) {
    foreach ($j in $jobs) {
        & schtasks /Delete /TN $j.Name /F 2>$null | Out-Null
        Write-Host "已删除: $($j.Name)"
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
Write-Host "下一步：把常驻服务改为仅 ticker（10s 轮询仍需常驻）："
Write-Host "    myenv\Scripts\python.exe -u scripts\run_service.py --ticker-only"
Write-Host "验证：schtasks /Query /TN TraderSystem_evening_report"
