# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    停用 Hermes 桌面端对飞书应用的连接（同一应用长连接是集群模式，只能保留一个客户端）。
    本项目 invest.push.feishu_ws 直连飞书后，必须停用 Hermes 侧连接，否则消息随机分流
    → 艾特机器人经常没回应。本脚本只注释掉 Hermes .env 里的 FEISHU_* 配置，不删除任何文件。

    用法（管理员 PowerShell）：
        powershell -ExecutionPolicy Bypass -File scripts\disable_hermes_feishu.ps1

    恢复：撤销生成的备份文件即可（见输出里的 Backup 路径）。
#>
$ErrorActionPreference = "Stop"

$hermesEnv = "E:\Hermes Agent CN Desktop\data\hermes-home\.env"
if (-not (Test-Path -LiteralPath $hermesEnv)) {
    Write-Host "未找到 Hermes .env: $hermesEnv（Hermes 可能未安装或路径不同，跳过）"
    exit 0
}

# 备份
$backup = "$hermesEnv.backup_pre_disable_feishu_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
Copy-Item -LiteralPath $hermesEnv -Destination $backup
Write-Host "已备份: $backup"

$lines = Get-Content -LiteralPath $hermesEnv -Encoding UTF8
$changed = 0
$out = foreach ($line in $lines) {
    # 匹配 FEISHU_ 开头的配置行（含 FEISHU_CONNECTION_MODE=websocket 等）
    if ($line -match '^\s*FEISHU_' -and $line -notmatch '^\s*#') {
        $changed++
        "#[DISABLED-BY-TRADERSYSTEM] $line"
    } else {
        $line
    }
}
Set-Content -LiteralPath $hermesEnv -Value $out -Encoding UTF8
Write-Host "已注释 FEISHU_* 配置 $changed 行（Hermes 重启后将不再连接飞书应用）"
Write-Host "下一步：重启 Hermes 桌面端，或杀掉 hermes-agent-cn-desktop 相关进程使其生效。"
Write-Host "验证：Hermes 日志不再出现 [Lark] connected to wss://msg-frontier.feishu.cn 即成功。"
