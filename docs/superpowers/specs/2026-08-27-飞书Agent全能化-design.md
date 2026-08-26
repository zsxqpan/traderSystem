# 飞书 Agent 全能化设计（2026-08-27）

## 背景与问题

飞书 Agent（Trader-Fox）按 `_is_finance()` 关键词把问题分流到 CHAT_SYSTEM（金融，≤200 字、工具 ≤2 次）
或 GENERAL_SYSTEM（通用，**禁止调用行情/股票工具**）。实测同一问题「分析华工科技半年报」：

- `_is_finance("分析华工科技半年报") = False`（"半年报/业绩/环比"等词不在判别表）→ 走 GENERAL_SYSTEM；
- GENERAL_SYSTEM 明令禁止调用行情工具 → 模型凭记忆编造数据（"Q2 净利 7.6 亿、环比 +77%"，
  真实是 5.48 亿、**环比 -14%**，方向相反；chat_history 139 条记录实锤）；
- 用户在飞书日常使用 grill-me / debug / 头脑风暴等工作流，这些 skill 只存在于 `.dsh/skills/` 文件，
  提示词完全未接入 → 模型即兴发挥、可用性差。

## 目标

**飞书里所有问题都使用 Agent 完整能力**：25 个工具全开放、全部 skill（角度 + 工程 + 方法论）可调用、
分析类回答不再被 200 字/2 次工具压扁、防幻觉纪律保留。

## 设计

### 1. 统一全能提示词（agents.py）

- `CHAT_SYSTEM` 重写为全能 Agent 提示词，`run_chat` **不再分流**，一律使用 CHAT_SYSTEM；
- `GENERAL_SYSTEM` 保留常量但不再被引用（外部兼容）；
- 结构：角色 → 输出风格（快答 ≤300 字 / 分析·工作流 600-1500 字）→ 工具全集（25 个）→
  Skill 机制（角度 8 skill 摘要 + 工程 skill 触发词 + load_skill 用法 + 自标注）→ 分层纪律：
  - A 金融数据纪律（涉及行情/个股/财务时激活：新鲜度先行、只引用工具数据、实时价重取、
    龙虎榜 query_lhb、搜索重试、数据失效防守、UZI 门禁——沿用旧 CHAT_SYSTEM 有效规则）；
  - B 通用纪律（常识/人物/百科：web_search 确认、不确定如实说）；
  - C 工作流纪律（grill/debug/brainstorming：先 load_skill 拿方法论再执行）；
  - D 防幻觉总条款（数字必须来自工具/搜索；推算必须说明口径与误差）；
  - E 多轮记忆（保留旧规则）；F 不自动交易。
- `_FINANCE_RE` 补财报类词：`半年报|中报|年报|季报|一季报|三季报|业绩|营收|净利|利润|季度|环比|同比|财报|公告|公司`；
  该函数保留（供测试/报告请求识别），但不再决定是否降级。

### 2. 参数放开

- `run_chat`：`max_turns=3 → 6`（复杂分析多轮工具）；
- 历史记忆：`_CHAT_HISTORY_LIMIT 8 → 12`、`_CHAT_HISTORY_MAX_CHARS 3000 → 6000`；
- `_save_history`：assistant 2000 → 4000、user 1000 → 2000；
- `feishu_ws._CHAT_MAX_LEN`：2000 → 6000（飞书消息截断保护同步放大）。

### 3. load_skill 工具（tools.py）

- 新增工具 `load_skill(name)`：读 `.dsh/skills/<name>/SKILL.md`（回退 `.claude/skills/`）全文返回
  （截断 12000 字符）；未知名返回 error 并附可用清单（模型可自纠正）；内置别名：
  `debug/诊断/调试→systemdebugging`、`grill/grillme/拷问→grill-me`、`brainstorm/头脑风暴→brainstorming`；
- 注册进 TOOL_SCHEMAS（含工程 skill 触发词说明）+ `_IMPLEMENTATIONS` + `no_conn`（不绑 conn）；
- 顺带修复既有小 bug：`xueqiu_search` 加入 `no_conn`（否则 partial(conn) 后调用会 TypeError）。

### 4. 顺带修复

- `llm.py::_maybe_alert_usage`：`row["t"]` 在无 row_factory 连接上抛
  `tuple indices must be integers or slices, not str` → 兼容 tuple/Row 两种访问。

### 5. 成本与权限

- 全员全能力；非管理员每日 token 限额（`feishu_nonadmin_daily_token_limit`，默认 100 万/日）**保留不变**，
  超限仍只回额度提示（feishu_ws._nonadmin_budget_exceeded）。

## 文件改动清单

| 文件 | 改动 |
|---|---|
| `invest/agent/agents.py` | CHAT_SYSTEM 重写、run_chat 不分流、max_turns/历史/保存上限、_FINANCE_RE 补词 |
| `invest/agent/tools.py` | load_skill 函数 + schema + 注册 + no_conn 修正 |
| `invest/push/feishu_ws.py` | _CHAT_MAX_LEN 2000→6000 |
| `invest/agent/llm.py` | 用量告警 row 兼容 |
| `tests/test_agent.py` | 路由测试改全能断言 + load_skill 测试 + run_chat 用 CHAT_SYSTEM 断言 |
| `AGENTS.md` | 「对话分流」段落更新为「全能 Agent」 |

## 测试

- `test_chat_route_finance_vs_general` → 改为：财报词命中 + run_chat 恒用 CHAT_SYSTEM（fake LLM 断言 system）；
- 新增 `test_load_skill`：grill-me 返回全文、未知名返回 error 带清单、别名 debug→systemdebugging；
- `test_chat_system_has_*` 系列保留（新提示词保持既有断言标记）；
- 跑 `pytest tests/test_agent.py tests/test_feishu_ws.py` + `ruff check invest tests`。

## 部署注意

- 改 agents.py 后**必须重启飞书服务**（pythonw 常驻进程）才加载新提示词（AGENTS.md 既有约定）。
