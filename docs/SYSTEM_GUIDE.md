# A股投资系统 · 系统介绍与使用说明

> 版本：2026-08-28 | 仓库：traderSystem | 环境：Windows 本机 + Python 3.14（`myenv` / `.venv`）
> 定位：**决策辅助，不自动交易**。规则算数与送达，AI 整理证据，人做中期比价。
> 部署默认：**ticker-only**（盘中 10s 轮询 + 每分钟补偿扫描 + 飞书接收）；13 个例行任务由 OS 计划任务触发。

---

## 1. 系统是做什么的

日常链路仍是：数据采集 → 定量计算 → 观点推理 → 执行纪律 → 复盘。

2026-08-28 起明确三件事：

1. **任务必须送达可查**：竞价、晚报、盘中报告等不再「函数跑完就算成功」。成功、失败、漏跑、限频、数据不足分开记账，可补偿或告警。
2. **行情必须逐标的完整**：新浪部分成功后继续补腾讯/东财；每个请求标的都有「实时 / 过期实时 / 最近收盘 / 停牌 / 缺历史 / 源失败」，报告不再静默缺行。
3. **比价交给人**：系统按行业生成结构化事实卡（强度、轮动、估值、拥挤、资金、周期、宏观减法 + 近 3–7 日带来源的消息）。AI **不产出综合买卖排名**。人在仪表盘「中期比价」并排比较、写结论，再决定是否入池/建卡。

飞书日常对话走本地意图 + 计划驱动：明确报价由代码先取实时价，回答必须引用证据；没有有效证据就标缺口，不编数字。

---

## 2. 日常怎么用

### 2.1 飞书（最常用入口）

| 场景 | 怎么说 | 系统做什么 |
|---|---|---|
| 盘中报告 | 「盘中报告」「来一份实时报告」 | 冻结统一快照后出报告；说「简洁/简短」才发简洁版 |
| 实时报价 | 「茅台现价」「宁德时代现在行情怎么样」「上证 000001 现价」 | 本地判为报价，强制走统一行情契约 |
| 系统状态 | 「数据新鲜吗」「任务跑了没」 | 查日线/指数新鲜度 + 报告任务账本 |
| 证据核对 | 把推送里的 `EVID-20260827-0001` 发给机器人 | 用 `query_evidence` 查摘要、来源、所属卡 |
| 普通问答 | 财报、板块、闲聊 | 未命中本地规则才走 `run_chat`；行情/财报/新闻须引用证据 |

约定：

- **私聊**：每条都回。
- **群聊**：只有 @ 机器人才回。未 @ 即使写了「盘中/报告」也不回。
- **限频**：盘中报告同一发送者间隔 **120 秒**；普通会话 **10 秒**。命中会明确提示，不静默丢消息。
- **群聊记忆**按 `chat_id + sender_id` 隔离，不会把别人的上下文串过来。
- 先回 ❤️ 表示已收到（需开通 `im:message.reaction`）。
- 非管理员每日 token 默认 100 万（`FEISHU_NONADMIN_DAILY_TOKEN_LIMIT`）。

不要把「今天半导体怎么样」当成盘中报告；那是普通问答。不要把「来一份盘中报告，顺便看下茅台现价」当成只查报价——明确「盘中报告」优先。

### 2.2 仪表盘「中期比价」

启动：`scripts\run_dashboard.py` → http://localhost:8501 → 左侧 **中期比价**。

1. 时点 `as_of` 留空 = **最新已落库事实卡日期**（不是「今天」）。
2. 按维度筛选：strength / rotation / valuation / crowding / capital / cycle / macro。
3. 用 `EVID-YYYYMMDD-xxxx` 检索证据。
4. 勾选 **3–5 个行业** →「深查并落库」（个股默认从候选池取，最多 20 只，按 as_of 截断）。
5. 并排看维度、展开证据/缺口 → 写下结论与备注。
6. 人工判断后再「送入候选池」或「生成机会卡片」（代码会归一化，`600519.SH` 与 `600519` 同一只）。

系统不在此页给出买卖排名。21:50 `factcard_refresh` 只在相对上一时点有重要变化时推送摘要 + 证据编号。

### 2.3 你会收到哪些报告

| 时间 | 名称 | 要点 |
|---|---|---|
| 交易日 08:40 | 盘前早报 | 隔夜外围、消息、关注 |
| 交易日 09:26 | 竞价报告 | 先冻 9:25 快照；过窗漏跑只告警，**不用盘中价伪装竞价** |
| 按需 | 盘中报告 | 指数/ETF/核心池同一 `as_of`；覆盖不足只出事实表 + 告警，不出「日内主线」 |
| 交易日 22:00 | 晚间盘后报告 | 一天只这一份（已合并原 16:00 日报 / 21:35 简报 / 22:00 复盘） |
| 周日 20:00 | 周报 | 消息面 + 周度复盘 |
| 交易日 21:50 | 事实卡变化 | 仅重要变化 + `EVID-` 编号 |

盘中/竞价：关键标的覆盖率不够会降级；生成失败、数据不足、发送失败、限频、成功是五种不同状态。板块/资金若来自收盘数据，会标「收盘参考·非实时」。

### 2.4 任务有没有跑成功

不要只看「服务还在」。查：

- `job_executions`：`job + 日期 + 槽位`，状态 `ok / failed / missed / running` 等
- `delivery_receipts`：飞书 / 企微 / 微信逐通道；超时断连记 uncertain，不自动盲重发
- 飞书问「系统状态」或仪表盘「数据状态」

窗口内失败会由每分钟补偿扫描重试；窗口外只记 `missed` 并告警。

---

## 3. 系统能力（现状）

### 3.1 数据与行情

- 实时：新浪 → 腾讯 → 东财 push2，**按标的**补源，10 秒轮询；统一契约 `invest/data/quotes.py`
- 日线：收盘 15:01 写 snapshot；晚间 akshare 覆盖；主复权新浪 qfq；PK 含 `src`
- 行业指数（同花顺，约 90 行业）、PE（巨潮）、龙虎榜、两融、涨停池、宏观（PMI/货币/社融/10Y/全A PE）
- PIT 四状态、候选决策留痕、行业映射 `data/industry_stocks.json`

### 3.2 定量与纪律

- 短线 RS / 动量 / 趋势阶段；中线周强度、拥挤度、估值分位、宏观流动性
- 轮动、温度、资金风格、联动网络
- 候选池硬门槛 + 容量 20/核心 10；价差、机会卡片、四套周期自动化
- 宏观总闸、环境重评、固定风险 R、凯利（样本够才启用）、回撤阶梯

### 3.3 推理与复盘

- 飞书 Agent：本地意图 → 代码计划 → 工具 → 组织答案；证据 ID 强制校验
- 双 Agent（投研/交易）+ 工单 + 仲裁仍保留
- 周/月/年复盘、BCS/VMS

### 3.4 自动化

- ticker-only 常驻 + **13** 个 OS 任务（见第 5 节）
- 推送：企微 / 飞书 / 个人微信；逐通道回执
- 证据驾驶舱：`fact_cards` / `fact_evidence` / `comparison_records`

---

## 4. 目录结构

```
traderSystem/
├── invest/
│   ├── data/          # 采集、统一行情 quotes.py、PIT
│   ├── quant/         # 强度 / 轮动 / 温度 / 资金 / 估值
│   ├── discipline/    # 池 / 价差 / 卡片 / 风控
│   ├── review/        # 周月年复盘
│   ├── agent/         # 对话编排、工具、证据校验
│   ├── skills/        # 报告 manifest / 快照 / 小节
│   ├── evidence/      # 事实卡、发现器、深查、变化推送
│   ├── push/          # 飞书 WS / 发送
│   ├── scheduler.py   # ticker + JOB_FUNCS + 补偿扫描
│   ├── delivery.py    # 投递三态与回执
│   └── db.py          # Schema（含 job_executions / fact_*）
├── dashboard/         # Streamlit，9 页（含中期比价）
├── scripts/           # run_service / run_job / install_os_tasks / init_db
├── tests/             # pytest（含 test_eval_e2e 评测集）
├── data/invest.db
└── docs/              # 本文件 + OPERATIONS.md
```

---

## 5. 调度任务（默认 OS + ticker-only）

`run_service.py` **默认 ticker-only**。先跑 `scripts/install_os_tasks.ps1` 注册 13 个任务。加 `--full` 才用完整 APScheduler，两种模式不要同时开。

| 时间 | 任务 | 说明 |
|---|---|---|
| 交易日 08:30 | premarket | 采集 + 定量 + 环境重评 + 盘前推送 |
| 交易日 08:40 | morning_brief | 盘前早报 |
| 交易日 09:26 | auction | 竞价报告；过窗不补发盘中数据 |
| 盘中 10 秒 | ticker | 三源行情 + P0 监控（常驻服务，不是 OS 任务） |
| 交易日 15:01 | snapshot_close | 收盘即日线 |
| 交易日 16:00 | after_close | 采集 + Agent + 扫描；**不再推日报** |
| 交易日 17:10 | pool_trap_scan | 候选池杀猪盘 |
| 周日 20:00 | weekend | 周报 + 周度复盘 |
| 每月 1 日 09:30 | monthly | 月度复盘 |
| 每年 1 月 1 日 09:30 | yearly | 年度复盘 |
| 交易日 21:30 | industry_refresh | 行业数据 + 定量 |
| 交易日 21:40 | daily_refresh | 日线/指数补采（晚报数据） |
| 交易日 21:50 | factcard_refresh | 事实卡；仅推重要变化 |
| 每日 22:00 | evening_report | 晚间盘后报告；**补偿按交易日**，周末不补发 |

休眠保活：`InvestSystemWake`（工作日 08:25/15:55、每日 21:58）+ `wake_guard.ps1`。

---

## 6. 常用命令

前缀：`myenv\Scripts\python.exe`（工作树开发可用 `.venv\bin\python`）。

| 目的 | 命令 |
|---|---|
| 初始化/升级库表 | `scripts\init_db.py` |
| 注册 OS 任务 | `powershell -File scripts\install_os_tasks.ps1` |
| 跑单个例行任务 | `scripts\run_job.py auction`（名称见 `JOB_FUNCS`） |
| 全量采集 | `scripts\collect.py` |
| 单段流水线 | `scripts\run_pipeline.py collect`（quant / premarket / after_close / weekend） |
| 历史回填 | `scripts\backfill.py 20200101` |
| 候选池 CLI | `scripts\discipline.py pool add 600519 --level core --industry 白酒` |
| 复盘 | `scripts\run_review.py weekly` |
| 仪表盘 | `scripts\run_dashboard.py` |
| 常驻服务 | `scripts\run_service.py`（`--full` 为完整调度） |
| 行为评测 | `python -m pytest tests/test_eval_e2e.py` |
| 全量测试 | `python -m pytest tests/` |

---

## 7. 数据与配置

### 7.1 `.env`（勿提交）

```ini
LLM_API_KEY=你的DeepSeek密钥
TUSHARE_TOKEN=你的tushare token
WECOM_WEBHOOK=企业微信机器人地址
FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID / FEISHU_OWNER_OPEN_ID
WEIXIN_TOKEN / WEIXIN_TO_USER_ID / WEIXIN_CTX_PATH
DB_PATH=data/invest.db
DAILY_LLM_BUDGET_TOKENS=60000
```

### 7.2 库表（`data/invest.db`）

行情：`daily_bars` / `index_bars` / `industry_bars`。纪律：`candidate_pool` / `cards` / `trade_plans`。  
送达：`job_runs` / `job_executions` / `delivery_receipts`。  
驾驶舱：`fact_cards` / `fact_evidence` / `comparison_records`。  
升级后必须再跑一次 `init_db`，新表才会出现在真实库。

---

## 8. 仪表盘（9 页）

- **市场总览** / **轮动与联动** / **短线轨** / **中线轨**
- **中期比价**（见 §2.2）
- **观点库** / **执行纪律** / **回测** / **数据状态**

---

## 9. 推送与飞书接入

| 级别 | 触发 | 限频 |
|---|---|---|
| P0 | 盘中异动（主板 ±3% / 创业板·科创板 ±6%）、止损、数据失效 | 同标的 1800s；数据失效边沿触发 |
| P1 | 收盘扫描变化 | 600s |
| P2 | 22:00 晚报 | 600s；日线未到最近交易日则只推滞后原因 |
| 其他 | 竞价 / 周报 / 事实卡变化 / 任务失败 | 见调度表 |

飞书长连接（lark-oapi）：开发者后台用**长连接**订阅 `im.message.receive_v1`；同一应用同时只能有一个客户端。启用本服务前停掉 Hermes 或其他飞书长连接。

未配置 webhook 时该通道自动关闭，不影响采集和定量。

---

## 10. 已知边界

- 只辅助决策，不自动交易
- 新闻进事实卡必须有引擎/页面级发布日；必应摘要前缀日期不算发布日，可能记 `missing news`
- 凯利/BCS 缺真实成交样本
- 行业 PB 数据源仍待接入
- 全量 pytest 在缺 skill 目录、`industry_stocks.json`、真实 `invest.db` 的工作树里会有环境失败，以本机完整数据环境为准

---

## 11. 新机器上手

```bat
cd /d C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem
myenv\Scripts\python.exe -m pip install -r requirements.txt
:: 配置 .env
myenv\Scripts\python.exe scripts\init_db.py
myenv\Scripts\python.exe scripts\collect.py
myenv\Scripts\python.exe scripts\run_pipeline.py quant
powershell -File scripts\install_os_tasks.ps1
myenv\Scripts\python.exe scripts\run_dashboard.py
myenv\Scripts\python.exe scripts\run_service.py
```

运维与排障见 `docs/OPERATIONS.md`。待办见 `TODO.md`。
