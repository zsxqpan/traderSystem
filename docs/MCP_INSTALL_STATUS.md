# MCP / Skill 安装状态与恢复指引（2026-08-16 最终）

> 客户端：**Codex**（config.toml 配置）+ Hermes / DeepSeek Harness。
> 四个 Skills 已克隆到 `.claude/skills/`；三个 MCP 已在 Codex 启用并验证可用。
> 说明：`.mcp.json` 是 Claude Code 格式，本机不用 Claude Code，已删除；
> MCP 统一配置在 `~/.codex/config.toml` 的 `[mcp_servers.*]` 段。

## ✅ 已安装且验证可用（Codex）

| MCP | 配置（~/.codex/config.toml） | 验证 |
|---|---|---|
| **sqlite-invest** | `command = ...mcp-notify\Scripts\python.exe` + `args = ["-m","tools.mcp.local_sqlite_mcp"]` + `env.PYTHONPATH=traderSystem` | ✅ 已实查 invest.db（industry_bars 144,360 行） |
| **mcp-notify** | `command = ...mcp-notify\Scripts\mcp-notify.exe` | ✅ enabled |
| **akshare-tools** | `command = ...akshare-tools\Scripts\akshare-tools.exe` | ✅ enabled（FastMCP 3.4.7 banner 正常） |

验证方式：`codex mcp list`；在 Codex 里直接问"查 invest.db 表行数"即走 sqlite-invest。

## ✅ Skills（`.claude/skills/`，900+ 文件）

| Skill | 说明 |
|---|---|
| A-Stock-Skills | 6 技能已展开到 `.claude/skills/` 根 |
| claude-for-financial-services-cn | 中文 A股（plugins/vertical-plugins） |
| anthropic-financial-services | 官方垂直插件式 |
| finance-quant-skills | A股量化 13 子技能 |

## ⚠️ 安装中发现的坑（记录在案）

1. **tushare-mcp-server 上游包有 bug**：要求 `mcp>=1.0` 却用 `Server.list_tools` 旧 API，装上即崩——放弃，用 akshare-tools 的 Tushare 接口替代。
2. **MCP Inspector（npm）装坏**：npm optional dependencies bug（[npm/cli#4828](https://github.com/npm/cli/issues/4828)），与 server 无关，可跳过。
3. 沙箱（DeepSeek Harness 执行环境）禁 stdio 子进程管道，MCP 握手只能在用户本机终端/Codex 验证——已在 Codex 完成。
4. `uv tool install` 一次只装一个包；工具 exe 在 `AppData\Roaming\uv\tools\<pkg>\Scripts\`，config 里用全路径最稳。

## 若以后要用 Hermes / DeepSeek Harness 接 MCP

- Hermes：在其配置/设置里找 MCP 或插件入口，把同样的 `command` 填进去（Hermes 是桌面 Agent 框架，配置方式独立于 Codex）。
- DeepSeek Harness：本 GUI 的 MCP 接入需看其是否暴露 MCP 客户端配置；当前 Codex 已满足主要用途。

