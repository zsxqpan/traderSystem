# 网关不稳定根因分析：飞书艾特机器人经常没回应

> 分析日期：2026-08-18　分析对象：`invest/push/feishu_ws.py` + `feishu_push.py` + Hermes 桌面端
> 结论先行：**不是单一故障，而是“双客户端抢消息 + 单点静默失败”叠加**。其中
> **Hermes 桌面端与本项目用同一飞书应用同时建立长连接**是“经常没回应”的最大根因。

---

## 0. 现状链路

```
用户在飞书群 @Trader-Fox 机器人
        │
        ▼
飞书开放平台（长连接集群模式：同一应用 N 个客户端，事件只投递给其中随机 1 个）
        │
        ├──► Hermes 桌面端客户端（FEISHU_APP_ID=cli_aa0abf5ab7399bd8，websocket 模式）※仍在运行
        │         └─► Hermes agent 处理（或 whitelist-guard 插件崩溃 → 无回应）
        │
        └──► 本项目 feishu_ws 客户端（同一 cli_aa0abf5ab7399bd8）
                  └─► 仅管理员 + 意图判定通过 → 生成报告并回复
```

实测证据（2026-08-18）：

| 证据 | 位置 |
|---|---|
| Hermes 桌面端进程存活 | `hermes-agent-cn-desktop.exe`（8/16 23:24 启动）+ 2 个 runtime 进程 |
| Hermes 配置同一飞书应用且为 websocket 模式 | `E:\Hermes Agent CN Desktop\data\hermes-home\.env`：`FEISHU_APP_ID=cli_aa0abf5ab7399bd8`、`FEISHU_CONNECTION_MODE=websocket` |
| Hermes 活跃接收飞书消息 | `feishu_seen_message_ids.json` 持续写入消息 id（8/18 仍在更新） |
| Hermes 网关自身崩溃 | `logs/errors.log`：`AttributeError: 'MessageEvent' object has no attribute 'get'`（whitelist-guard 插件 `_on_pre_gateway_dispatch` 崩溃） |
| Hermes 网关断线重连 | `desktop-gateway-restart.log`：`receive message loop exit, err: no close frame received or sent` → `trying to reconnect` |
| 本项目连接成功 | `logs/service.log`：`[Lark] connected to wss://msg-frontier.feishu.cn/ws/v2?...` |
| 本项目旧版曾走 127.0.0.1:7892 代理失败 | service.log：`ProxyError ... 127.0.0.1:7892 ... 目标计算机积极拒绝`（8/17 版本，已修复为直连） |
| Hermes 在主动管理本项目服务 | Hermes errors.log：agent 通过终端执行 `Stop-Process`/重启 `run_service.py`（8/18 19:08–19:11） |

---

## 1. 根因排序

### 1.1 【最大根因】双客户端抢消息：Hermes 与本项目同时连同一飞书应用

- 飞书**长连接是集群模式**：同一应用（同一 `app_id`）可以有多个 WS 客户端，但**每条事件只会随机投递给其中一个客户端**，不会广播。
- 本项目 `feishu_ws.py` 与 Hermes 桌面端**用的是同一个应用 `cli_aa0abf5ab7399bd8`**，且都处于 websocket 长连接模式 → 群里每条消息 50% 概率落到 Hermes，50% 落到本项目。
- 落到 Hermes 时：Hermes 的 agent 会按它自己的逻辑处理（它的 `FEISHU_HOME_CHANNEL` 恰巧是同一个 open_id），而它的网关本身还在崩（见 1.2），或回复了别的形态——但**本项目机器人不会回应**。
- 落到本项目时：还要过“仅管理员 + 意图判定”两道关（见 1.3）才回应。
- 结果：用户感觉“**经常**（随机、时好时坏）艾特没回应”。这正是集群随机分流的典型症状。
- 本项目文档早已预警：`feishu_ws.py` 头部注释与 `docs/SYSTEM_GUIDE.md` §7.1 都写了“启用本项目连接前请停用 Hermes 对该应用的飞书连接，避免消息随机分流”，但 **Hermes 至今未停用**。

### 1.2 Hermes 网关自身不稳（雪上加霜）

即使消息落到 Hermes，Hermes 侧也不可靠：
- `whitelist-guard` 插件在消息分发前崩溃（`'MessageEvent' object has no attribute 'get'`）→ 事件处理中断；
- WS 连接无 close frame 断开 → 进入重连循环，窗口期丢消息；
- Hermes agent 的 LLM 调用（deepseek）出现 `APIConnectionError` 重试（errors.log 19:10:02）。

### 1.3 本项目回复链路是“单点静默”设计（修复前）

旧版 `_handle_event` 只有一条路径：

```
sender==owner 且 _is_report_request(text)（纯 LLM 语义判断）→ 回报告
其它任何情况 → return（无任何回应）
```

- **纯 LLM 判断**：DeepSeek 调用失败/超时 → `_is_report_request` 返回 False → **静默**；提示词过严（只认“当前实时报告”）→ 表述稍偏即误判为 no → **静默**；无关键词兜底、无重试。
- **不识别 @ 提及**：没有解析 `message.mentions`，艾特与不艾特一视同仁；非管理员艾特 → 永远静默。
- **发送无缓存无重试**：每条回复都重新换 tenant token + 发送，任一网络抖动即失败，且只在日志里 warning，群里无反馈。
- 报告生成耗时数秒，期间无 ack，用户连点后多线程并发生成。

### 1.4 重连/重启窗口丢消息

- lark SDK 断线自动重连，但**断线到重连之间的消息不回补**（长连接无离线补投）。
- `run_service.py` 对 feishu_ws 的守护是“退出后 10–30s 重启”，窗口期内群消息全部丢失。
- Hermes agent 还曾主动 kill/重启本项目服务（errors.log 8/18 19:08–19:11），重启瞬间同样丢消息。

### 1.5 环境因素（已修，保留记录）

- 8/17 前 `feishu_push` 走 `FEISHU_PROXY=http://127.0.0.1:7892`，代理未开时 token 获取全部失败 → 推送全挂（service.log 有 ProxyError 记录）。8/16 起已改为 `trust_env=False` 直连，**若再出现全量失败先查代理软件/系统代理残留**。

---

## 2. 修复措施（本次已落地）

| # | 措施 | 文件 |
|---|---|---|
| 1 | **@ 提及识别**：解析 `message.mentions`，艾特机器人必有回应（管理员回报告/帮助，非管理员回权限提示）。**实测 lark-oapi 事件里是 `MentionEvent`，其 `id` 为 `UserId` 对象（`.open_id` 属性），`_mention_ids` 已兼容 str / dict / UserId 对象三形态 + 文本 `@_user` 占位符兜底**（首版实现按字符串比较导致 `@在吗` 无回应，2026-08-18 实测修复，日志确认 `mentioned=True`） | `invest/push/feishu_ws.py` |
| 2 | **意图判定双保险**：本地关键词规则先判（零成本），规则未命中才走 LLM；LLM 失败/超时回落规则结论，不再静默 | `invest/push/feishu_ws.py` |
| 3 | **管理员请求 ack**：先回“⏳ 收到，正在生成…”再生成报告，避免数秒空白 | `invest/push/feishu_ws.py` |
| 4 | **限频**：同一发送者 30s 内不重复回复，防连点刷屏与并发生成 | `invest/push/feishu_ws.py` |
| 5 | **tenant token 缓存**：2h 有效、提前 60s 过期，避免每条消息重复换取 | `invest/push/feishu_push.py` |
| 6 | **发送重试 1 次**：失败换新 token 重发，吞掉网络抖动 | `invest/push/feishu_push.py` |
| 7 | **停用 Hermes 同应用连接**（需执行，见下） | `scripts/disable_hermes_feishu.ps1` |

## 3. 必须执行的部署步骤（否则 1.1 依旧）

1. **停用 Hermes 对同一飞书应用的连接**（二选一）：
   - 运行（任意目录可执行）：
     ```powershell
     powershell -ExecutionPolicy Bypass -File "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem\scripts\disable_hermes_feishu.ps1"
     ```
     脚本会备份并注释 Hermes `.env` 的 `FEISHU_*`，然后重启 Hermes 桌面端使其生效；或
   - 在 Hermes 设置里删除该飞书应用的绑定。
2. **重启本项目服务**使新代码生效：
   ```bat
   myenv\Scripts\python.exe scripts\run_service.py
   ```
   （或双击 `scripts\start_service.ps1`；单实例锁会保证不重复启动）
3. 验证：`logs/service.log` 出现 `[Lark] connected to wss://msg-frontier.feishu.cn` 且 **Hermes 日志不再出现同类连接**。

## 4. 后续建议（非本次范围）

- 飞书“接收消息”事件在断线窗口不补投，如需强可靠性可叠加“群消息落库 + 定时兜底扫描”（把收到的消息 id 存 `data/feishu_seen.json`，重连后对比缺口并用开放平台 API 拉取）。
- 考虑把 `_is_report_request` 的 LLM 调用接入 `LLMClient` 的预算控制（当前 `conn=None` 不记账，intent 判定不受每日 token 预算约束，属预期行为，但可记账便于观察）。
