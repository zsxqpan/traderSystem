# 去 Hermes 化改造记录（2026-08-18）

> 目标：本项目运行时不再依赖 Hermes 桌面端（agent）。凡依赖 Hermes 的功能全部改为
> 项目本体直连/自持数据；无法替代的能力单独列清单（见 §4）。

---

## 1. 改造前：本项目对 Hermes 的运行时依赖点

| # | 依赖点 | 原实现 | 现状 |
|---|---|---|---|
| 1 | 飞书群消息接收（群内触发盘中报告） | 轮询 Hermes 桌面端 `gateway.log`；Hermes 网关不稳导致“艾特没回应” | ✅ 已改：`invest/push/feishu_ws.py`（lark-oapi 官方 WebSocket 长连接，项目本体直连） |
| 2 | 个人微信推送的会话凭据 | `weixin_push.py` 读取 `E:\Hermes Agent CN Desktop\data\hermes-home\weixin\accounts\...context-tokens.json`（Hermes weixin 登录产物） | ✅ 本次迁移：凭据复制到项目本地 `data/weixin/context-tokens.json`，默认不再读 E 盘 |
| 3 | Hermes 桌面端对本项目服务的“运维”（agent 通过终端 kill/重启 run_service、改 .env） | Hermes agent 会话里手工操作 | ⚠️ 行为在 Hermes 侧，需停用/约束（见 §3） |
| 4 | Hermes 与项目共用同一飞书应用长连接 | Hermes `.env` 配 `FEISHU_APP_ID=cli_aa0abf5ab7399bd8` + `FEISHU_CONNECTION_MODE=websocket` | ⚠️ 集群模式随机分流 → 必须停用 Hermes 侧连接（`scripts/disable_hermes_feishu.ps1`） |
| 5 | `tools/hermes_skills/`（serenity / UZI / stock-analysis 等第三方 skill 克隆） | 供 Hermes 桌面端安装使用 | ✅ 非运行时依赖：项目代码从不 import 它们。保留作资产或按需归档（见 §5） |
| 6 | 文档/注释中的 Hermes 指引 | `docs/SYSTEM_GUIDE.md` §7.1、`config.py` 注释、`report.py` docstring、README 等 | ✅ 本次清理 |

---

## 2. 本次改动清单（已落地）

### 2.1 飞书链路（网关稳定性修复，详见 `docs/GATEWAY_STABILITY_ANALYSIS.md`）
- `invest/push/feishu_ws.py`
  - 新增 **@ 提及识别**（解析 `message.mentions`）：艾特机器人必有回应，不再静默；
  - 意图判定**关键词规则 + LLM 双保险**，LLM 失败回落规则；
  - 管理员请求先回 **ack** 再生成报告；同一发送者 **30s 限频**；
  - 非管理员艾特 → 权限提示（报告含持仓等私有信息）。
- `invest/push/feishu_push.py`
  - tenant_access_token **2h 缓存**（提前 60s 过期），不再每条消息重新换取；
  - 发送失败**换新 token 重试 1 次**。

### 2.2 个人微信链路（去 Hermes 数据目录）
- `invest/push/weixin_push.py`
  - 默认 context-token 路径改为项目本地 `data/weixin/context-tokens.json`；
  - 新增 `migrate_context_tokens()`：首次读取时把旧 Hermes 目录的文件**一次性复制**进项目（项目文件已存在则不覆盖），此后与 Hermes 解耦；
  - `client_id` 前缀去掉 `hermes-`。
- `data/weixin/context-tokens.json`：已从 Hermes 目录复制（188B，内容不变）。
- `.env`：`WEIXIN_CTX_PATH` 改为 `data\weixin\context-tokens.json`。
- `invest/config.py`：相关注释更新。

### 2.3 运维与文档
- 新增 `scripts/disable_hermes_feishu.ps1`：备份并注释 Hermes `.env` 的 `FEISHU_*` 配置（需管理员权限执行）。
- 更新 `docs/SYSTEM_GUIDE.md` §7.1（见下）。
- `docs/GATEWAY_STABILITY_ANALYSIS.md`：网关不稳定根因分析。
- 本文件：去 Hermes 化改造记录 + 不可替代能力清单。

---

## 3. 部署步骤（按序执行）

1. **停用 Hermes 对飞书应用的连接**（消除双客户端抢消息；任意目录可执行）：
   ```powershell
   powershell -ExecutionPolicy Bypass -File "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem\scripts\disable_hermes_feishu.ps1"
   ```
   然后重启 Hermes 桌面端；或手动在 Hermes 设置中删除该飞书应用绑定。
2. **重启本项目服务**（加载新代码）：
   ```bat
   myenv\Scripts\python.exe scripts\run_service.py
   ```
3. **验证**：
   - `logs/service.log` 出现 `[Lark] connected to wss://msg-frontier.feishu.cn`；
   - Hermes 侧日志不再出现 `[Lark] connected`；
   - 群里 @机器人 → 必有回应（管理员=报告，非管理员=权限提示）。
4. （可选）确认微信推送仍正常：`Notifier` 三通道里微信通道发送一次即可。

---

## 4. 无法替代的功能清单（重要）

以下能力**无法**在本项目内等价自建，请按需决策：

| # | 能力 | 为什么无法替代 | 替代/妥协方案 |
|---|---|---|---|
| 1 | **个人微信 iLink Bot 的登录凭据获取**（`WEIXIN_TOKEN`、`context_token`） | 凭据只能通过微信 iLink Bot 协议登录获得。Hermes 只是曾经的“登录工具”；项目代码无法凭空签发微信机器人凭据。Hermes 停用后：存量凭据可继续用（已迁入项目），但**新账号/新会话对端**需要 iLink 官方登录流程（或重新启用某个登录工具） | 凭据已迁入 `data/weixin/`，存量会话不受影响；新对端需走 iLink 官方渠道 |
| 2 | **Hermes 桌面端作为通用 Agent 执行“自然语言指令 → 任意工具/技能”** | 群里 @ 机器人让它“随便做什么”（调脚本、跑技能、问历史）是 Hermes 的通用 agent 能力；本项目只实现固定命令流（关键词 → 盘中报告） | 已有 DeepSeek Harness（本开发环境）具备同能力；或在 feishu_ws 里扩展意图路由（把更多意图接到 pipeline/agent） |
| 3 | **Hermes 生态的第三方 skill 资产**（E 盘 `skills/finance/` 29 个；项目内 `tools/hermes_skills/` 4 个仓库） | 这些是 Hermes 生态的安装产物，不属于本项目的运行时能力 | 项目已克隆 4 个到 `tools/hermes_skills/`；如需在 Harness 使用，把对应 SKILL.md 复制到 Harness skills 目录即可（与 Hermes 无关） |
| 4 | **Hermes 桌面端 UI/会话管理/记忆/kanban 等产品功能** | 属于 Hermes 产品本身，非本项目功能 | 不需要替代（本项目不依赖）；如需要 agent 工作台，用 DeepSeek Harness |
| 5 | **Hermes 曾提供的“运维本项目服务”的便利**（agent 直接改 .env、重启服务） | 项目代码不依赖它；但如果你已习惯让 Hermes 帮你运维 | 改用项目自带脚本（`run_service.py` / `start_service.ps1` / `check_service.py`）或 DeepSeek Harness |

> 一句话总结：**本项目运行所需的一切（数据、调度、推送、飞书收发）都已不依赖 Hermes**；
> 唯一“不可替代”的是**微信机器人凭据的获取渠道**（存量可用、新会话需 iLink 官方流程）和
> **Hermes 作为通用 agent/工作台的产品能力**（用 DeepSeek Harness 替代）。

---

## 5. 遗留资产处置建议（按需，不影响运行）

- `tools/hermes_skills/`：4 个第三方 skill 仓库（serenity/UZI/stock-analysis）。不再向 Hermes 复制（`docs/SKILLS_MCP_INVENTORY.md` 已有记录）。建议保留在项目内作资产，或迁移到 Harness skills。
- `docs/` 中提及 Hermes 的历史文档（`GURU_SKILLS_SURVEY.md`、`MCP_INSTALL_STATUS.md`、`QUANTDASH_COMPARISON.md`、`TRADER_SKILLS_SURVEY.md`、`SKILLS_MCP_INVENTORY.md`）：均为调研/说明性质，保留即可，不构成运行时依赖。
