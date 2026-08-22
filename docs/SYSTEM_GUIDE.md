# A股投资系统 · 系统现状与使用说明

> 版本：2026-08-15 | 仓库：traderSystem | 环境：Windows 本机 + Python 3.14（`myenv` 虚拟环境）
> 定位：**决策辅助系统，不自动交易**。数据采集 → 定量计算 → 观点推理 → 执行纪律 → 复盘闭环。

---

## 1. 系统现状（2026-08-15 盘点）

### 1.1 完成度总览

| 维度 | 状态 |
|---|---|
| TODO 总项 | 83 项，✅ 已完成 75，剩 8（[C] 时间/运行依赖 2 项 + FastAPI 按需 + 少量待扩充样本项） |
| 代码可做项（[A] 12 项） | ✅ 全部完成（2026-08-15） |
| 用户执行项（[B] 8 项） | ✅ 完成 7 项（Tushare/社融/行业全量/龙虎榜/回填/pytest/环境重评）；FastAPI 按需 |
| pytest | 全量 **通过（EXITCODE=0）**，含 test_data/test_agent/test_api 真实网络用例 |
| 真实数据 | 90 个行业日线（2020-01 起）、全市场龙虎榜、宏观（PMI/货币/社融/10Y/全A PE）、行业 PE/PB 估值 |
| 调度服务 | 常驻 APScheduler，盘前/盘后/周末/月度/年度/盘中 4 秒行情/夜间复盘/P2 简报 9 个任务 |

### 1.2 已落地的核心能力

**数据层（invest/data）**
- 实时行情三源直连轮询：新浪 / 腾讯 / 东财 push2 多域名容灾，3-5 秒间隔，自动切换备用源并留痕；端到端延迟 ≤ 10 秒，新鲜度不合格即告警（`realtime.py`）
- 日线主源新浪 qfq（东财回退），备用 Tushare（token 已配置）+ 交叉校验
- 行业指数全量（同花顺，90 行业）、行业 PE（巨潮）+ PB（列已就绪，数据源接入后自动出分位）
- 龙虎榜 + 席位明细、两融、涨停/炸板情绪、宏观（PMI/货币供应/社融增量/新增信贷/10Y 国债/全A PE 分位）
- 数据底座 PIT 化：四状态质量检测（valid/delayed/stale/conflict）、溯源留痕、候选决策全量留存（`pit.py`）
- 个股→行业手工映射兜底（`data/industry_stocks.json` + `industry_map.py`）
- L3 主题/产业链清单（`data/themes.json` 首批 12 个 + `themes.py`）
- 历史行业归属/ST 状态每日快照（`universe.py` + `stock_universe_history` 表）

**定量层（invest/quant）**
- 短线轨：相对强度 RS（5/10/20 日超额）、多周期动量、趋势阶段（破位/背离/加速/减速/启动/震荡）
- 中线轨：周线强度、拥挤度（成交占比分位）+ 五态状态机、行业估值分位（PE/PB）、宏观流动性加工（M1-M2 剪刀差/PMI/社融增量）
- 板块轮动排名、市场温度（冷/中性/暖/热 + 宽度）、资金风格（主题炒作/产业趋势等）、行业联动网络
- 共线性控制（|ρ|>0.60 违规对降权）、四套权重冻结 + 季度样本外评估 + 规则版本管理

**比价与卡片（invest/discipline）**
- 对象池硬门槛（非 ST/上市 60 日/ADV 5000 万）+ 冻结名单 + 容量 20/核心 10
- 主价差分析：历史分位 + 稳健 Z（MAD）+ 回归锚区间（40-60%）；**结构断点检查**自动截断旧口径防假极值
- 榜单降级为「发现器」：过错价必要条件（分位 <30% 或 Z 显著为负）才入池，否决自动留痕
- 机会卡片：模板/状态机（candidate→locked→review→downgraded/void）/三句话验证/容量 20/赔率刚性顺序
- 因子打分 v1（0-5 分 × 角色权重）+ **四套周期镜像自动化**（波段/配置/事件博弈/趋势，`auto.py`）
- 宏观总闸：评级减法系数（宽松/中性 1.00、收紧 0.70）+ ERP 分位乘数 + 黑天鹅戒断（总闸减半/禁开仓/24h 复评）
- **环境重评触发**（数据驱动）：ERP 跨分位（全A PE 近10年分位 <0.2/>0.8）、社融增量环比转负、10Y 利率周变动 >20bp
- 仓位：固定风险 R（S/A/B=0.8/0.6/0.35%）+ 单笔硬上限（个股 10%/ETF 15%）
- 执行纪律：交易计划（必须候选池 + 必须止损）、成交偏差留痕、周期漂移检测（超期持有自动标记）
- 成本模型（佣金/印花税/过户费/滑点/冲击）、T+1/涨跌停/ADV 参与率校验、流动性违约冻结
- 组合风险：风险簇映射（12 簇）+ 跨周期敞口合并 + 预算上限（簇 40%/风格 60%/事件 20%）
- 凯利：Wilson 置信下界 × 1/6，n≥20 且凯利>0 才启用，否则回退固定风险
- 回撤限额阶梯（warn5%/reduce8%/clear12%/halt15%）+ 单日 2%/单周 4% 禁开仓 + 压力测试 5 场景

**推理与复盘（invest/agent / invest/review）**
- 双 Agent（投研/交易）+ 工单流转 + 自动仲裁；LLM 只引用工具返回数据，禁止编造；数据失效即防守硬约束
- 复盘：周度（纪律检查 + 持仓卡片复评）、月度（观点准确率 + 环境质量检查）、年度（等级单调性/凯利校准/权重区分度/规则归档/错误分类）
- 归因五维切片 + 错误分类五类 + BCS/VMS 双百分评估 + 一票否决

**自动化与推送（invest/scan / scheduler / notifier / push）**
- 收盘扫描：每日快照 JSON（PIT 前置）+ 变化检测（新入池/等级/评级变化）→ P1 推送，600s 限频
- **快照重建**：任意历史截面由最近快照复现（`rebuild_snapshot`）
- 推送时效分级：P0 盘中立即（300s 限频）/P1 收盘（600s）/P2 晚间汇总；多通道（企业微信/飞书/个人微信）
- P2 例行简报（每日榜单 + 宏观仪表盘），21:35 自动推送

---

## 2. 目录结构

```
traderSystem/
├── invest/               # 核心业务包
│   ├── data/             # 数据：采集/行情/行业/估值/PIT/历史快照/主题/映射
│   ├── quant/            # 定量：强度/轮动/温度/资金/联动/估值/拥挤度/宏观流动性
│   ├── discipline/       # 纪律：对象池/价差/卡片/仓位/计划/记录/风控/组合/凯利/自动化
│   ├── review/           # 复盘：周/月/年 + 归因 + 错误分类 + BCS/VMS
│   ├── agent/            # 双 Agent + 工单 + 仲裁 + LLM
│   ├── api/              # FastAPI 接口层（按需启动）
│   ├── push/             # 飞书 / 个人微信通道
│   ├── monitor.py        # P0 监控（止损/证伪/数据冲突）
│   ├── scan.py           # 收盘扫描 + 快照 + 变化检测 + 重建
│   ├── pipeline.py       # 全链路流水线 + 消息模板
│   ├── scheduler.py      # 调度器（9 个任务）
│   ├── notifier.py       # 多通道推送 + 限频
│   ├── governance.py     # 权重冻结/版本管理/OOS
│   └── db.py / config.py # 数据库 Schema / 配置
├── dashboard/            # Streamlit 仪表盘（8 页）
├── backtest/             # 轻量回测引擎 + 因子有效性评估
├── scripts/              # 手动执行入口（见第 5 节）
├── tests/                # pytest 全量（30+ 文件）
├── data/                 # SQLite 库 + 缓存 + 快照 + 手工映射
├── config/config.yaml    # 结构性参数
├── docs/                 # 本说明 + 运维手册 + 手动执行清单
├── .env                  # 密钥（已 gitignore）
└── TODO.md               # 完成度追踪
```

---

## 3. 数据与配置

### 3.1 .env（密钥，勿提交 git）
```ini
LLM_API_KEY=你的DeepSeek密钥
TUSHARE_TOKEN=你的tushare token      # 已配置，备用源 + 交叉校验
WECOM_WEBHOOK=企业微信机器人地址      # 推送（可选）
FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID   # 飞书群（可选）
WEIXIN_TOKEN / WEIXIN_TO_USER_ID / WEIXIN_CTX_PATH   # 个人微信（可选）
DB_PATH=data/invest.db
DAILY_LLM_BUDGET_TOKENS=60000
RISK_MAX_DRAWDOWN=0.15
```

### 3.2 config/config.yaml
- `rating_position_map`：评级-仓位映射（进攻/中性/防守 × 宽松/中性/收紧）
- `limits`：单票 15% / 行业 30% / 现金底 20% / 核心 10 / 池 20
- `indicators`：各定量指标参数（yaml 覆盖代码默认）
- `costs`：佣金/印花税/滑点等
- `budgets` + `clusters`：组合预算上限与风险簇规则（可追加覆盖）
- `breaks`：结构断点已知日期（行业/标的口径变化，可追加）

### 3.3 数据库（data/invest.db，SQLite WAL）
核心表：`daily_bars/index_bars/industry_bars`（行情）、`industry_valuation`（PE/PB）、`market_emotion`（情绪）、`macro_series`（宏观）、`dragon_tiger`（龙虎榜/席位）、`quant_*`（定量结果）、`viewpoints`（观点）、`tickets`（工单）、`candidate_pool/ratings/trade_plans/trade_records/risk_rules/cards`（纪律）、`candidate_decisions`（决策留痕）、`data_provenance`（溯源）、`stock_universe_history`（历史行业/ST）、`job_runs/review_reports/backtest_runs/rule_versions`（运行支撑）。

---

## 4. 调度任务（run_service.py 常驻，已注册开机自启）

| 时间 | 任务 | 说明 |
|---|---|---|
| 工作日 08:30 | premarket | 采集 + 定量 + **环境重评触发检查**（ERP/社融/10Y，触发即推送）+ 投研清单 + 盘前推送 |
| 工作日 08:40 | morning_brief | **盘前信息早报**：隔夜市场/龙虎榜资金焦点/板块主线/今日关注/宏观（简明扼要） |
| 盘中 4 秒 | intraday_tick | 三源实时行情轮询 + P0 监控（止损/证伪/数据冲突）；非交易时段守护空转 |
| 工作日 16:00 | after_close | 采集 + 定量 + 交易复盘 + 仲裁 + 盘后日报 + 收盘扫描（快照/P1）+ **历史行业/ST 快照** |
| 工作日 21:30 | industry_refresh | 同花顺当天行业数据刷新（晚间发布）+ 定量重算 |
| 工作日 21:35 | p2_brief | P2 例行简报（每日榜单 + 宏观仪表盘） |
| 每日 22:00 | nightly | 每日复盘推送 + 观点到期入队 + 工单超时检查 + 数据质量报告 |
| 周六 09:00 | weekend | 周报 + 周度纪律复盘（含周期漂移 + 持仓卡片复评） |
| 每月 1 日 09:30 | monthly | 月度观点复盘 + 环境质量检查 |
| 每年 1 月 1 日 | yearly | 年度规则复盘 |

休眠保活：系统计划任务 `InvestSystemWake`（工作日 08:25/15:55、每日 21:58 唤醒）+ `wake_guard.ps1` 守护调度服务；任务均设错失宽限（盘前/盘后 6h 等）。

---

## 5. 常用命令（项目根目录执行）

> 所有命令前缀 `myenv\Scripts\python.exe`

| 目的 | 命令 |
|---|---|
| 全量采集（含行业/龙虎榜/宏观） | `scripts\collect.py` |
| 单段流水线（collect/quant/…） | `scripts\run_pipeline.py collect`（quant/premarket/after_close/weekend） |
| 历史回填（2020 起点） | `scripts\backfill.py 20200101` |
| 情绪历史回填 | `scripts\backfill_emotion.py 60` |
| 行业 PE 历史回填 | `scripts\backfill_valuation.py 5` |
| 候选池/评级/计划 CLI | `scripts\discipline.py pool add 600519 --level core --industry 白酒` |
| 因子/价差自动化（四套周期镜像） | `scripts\run_auto.py factor` |
| 历史行业/ST 快照 | `scripts\run_auto.py universe` |
| 复盘（weekly/monthly/yearly） | `scripts\run_review.py weekly` |
| 仪表盘 | `scripts\run_dashboard.py` → http://localhost:8501 |
| API 服务（按需） | `scripts\run_api.py` → http://127.0.0.1:8000/docs |
| 常驻调度服务 | `scripts\run_service.py` |
| 全量测试 | `python -m pytest tests/` |
| 因子有效性评估 | `scripts\eval_factors.py` |
| 回测 | `scripts\run_backtest.py` |
| 行业连通性诊断 | `scripts\check_industry.py` |
| 实时行情端到端验证 | `scripts\verify_realtime_e2e.py` |
| 阶段 1 真实数据闭环 | `scripts\e2e_phase1.py` |
| 数据库备份 | `python -c "import sqlite3; c=sqlite3.connect('data/invest.db'); c.execute(\"VACUUM INTO 'backup_invest.db'\"); c.close()"` |

---

## 6. 仪表盘（Streamlit，8 页）

启动后左侧导航：
- **市场总览**：数据健康 / 温度趋势（近60日）/ 板块涨跌热力图 / 拥挤度×强度散点
- **轮动与联动**：轮动排名轨迹 / 联动网络图（阈值可调）/ 风格轮动时间线
- **短线轨**：温度 / 行业 RS 榜 / 资金属性 / 高相关对
- **中线轨**：周线 RS / 拥挤度 / 宏观流动性（M1-M2/PMI/社融增量）
- **观点库**：观点状态筛选 / 准确率
- **执行纪律**：评级→建议仓位 / 候选池 / 计划 / 交易记录
- **回测**：回测运行记录
- **数据状态**：各表覆盖 / 最近任务

---

## 7. 推送体系

| 级别 | 触发 | 通道 | 限频 |
|---|---|---|---|
| P0 | 盘中异动（**主板 ±3% / 创业板·科创板 ±6%**）/ 止损 / 数据失效 | 企业微信+飞书+微信 | **1800s（30 分钟）** |
| P1 | 盘中异动（track）/ 收盘扫描变化（新入池/等级/评级） | 同上 | 1800s / 600s |
| P2 | **晚间盘后报告（22:00，合并原 盘后日报/P2简报/每日复盘，只发一份）** | 同上 | 600s |
| 周末周报 | **周日 20:00**（消息面·**大模型提炼**近3日电报 + 周度复盘） | 同上 | 600s |
| 环境重评 | ERP 跨分位/社融拐点/10Y>20bp | 同上 | 12h |
| 盘中实时报告（按需） | 飞书群 @机器人（**纯 LLM 语义识别触发，无关键词**） | 飞书群 | 手动触发 |

**盘中节奏（2026-08-18 降频 / 2026-08-21 统一 30 分钟限频）**：实时行情轮询 **10 秒/次**（原 4s）；
异动推送 **P0/P1 统一 1800s（30 分钟）限频、按标的独立**——同一只票通知一次后 30 分钟内不再通知；
P0 数据失效告警改**边沿触发**——失效时通知一次、恢复时再通知一次（状态存 `data/monitor_state.json`）。

**盘后报告数据新鲜度门禁（2026-08-18）**：22:00 发送前先校验 `daily_bars`/`index_bars`
是否已更新到最近交易日（当日日线数据源晚间才发布，由 21:40 `daily_refresh` 补采）。
**数据滞后时不发报告**，改为推送一条滞后原因（含最新数据日期、最近交易日、补采任务是否执行），
12h 限频，并把原因写入 `job_runs` 留痕。

未配置 webhook 时推送自动禁用，不影响其它功能。

### 7.1 盘中实时报告机器人（飞书群 @ 触发）

在飞书群里 **@机器人**（管理员账号 `FEISHU_OWNER_OPEN_ID`）并发送
「盘中 / 实时 / 报告 / 行情 / 盘面」等请求，机器人自动回复盘中实时报告
（核心关注实时行情 + 涨跌幅 + 持仓警戒 + 温度 + 评级仓位建议）。
行为约定（2026-08-18 v4：私聊 + 群内任意 @ 全量 Agent 回应）：
- **私聊（p2p）**：任意消息都由 Agent 回应（报告/提问/闲聊）；
- **群内 @机器人**（管理员或其他人）：任意消息都由 Agent 回应（不再只回报告）；
- **回复分流**：语义识别为要实时报告 → 盘中实时报告（**默认简洁版**：核心池行情/板块异动/情绪人气/龙虎榜龙头/温度仓位/今日操作建议；消息里含「详细/完整」才发完整版，完整版管理员含持仓警戒）；问候/求助（在吗/你好/help）→ 帮助提示（本地判定零 token）；其他 → 会话 Agent（run_chat，带系统数据工具，max_turns=3）回答，**Skill 由大模型按语义自选**（Serenity/youzi/stock_analysis 方法论内置 prompt），模型在回复末尾自标注「↘ 已使用 Skill：xxx」；
- **收到消息先回 ❤️ 表情**（`im:message.reaction` 权限，需开发者后台开启），让你第一时间知道消息已收到；
- **纯 LLM 语义触发，无关键词**：是否生成盘中报告一律由 LLM 意图识别决定；管理员群内不 @ 的发言仅语义命中报告才触发；
- **非管理员每日 token 限额**：默认 100 万/天（`FEISHU_NONADMIN_DAILY_TOKEN_LIMIT`），私聊/群内 @ 的 LLM 用量记入 `llm_usage(job='group')`，超限只回额度提示；
- 限频：报告 30s/人，Agent 会话 10s/人；
- **盘中报告内容（2026-08-18 精简）**：不含指数强弱榜/市场风格；聚焦 板块异动（最近交易日涨幅TOP3）+ 情绪·人气（涨停/连板/炸板/情绪周期）+ 资金焦点·龙虎榜龙头 + 核心池实时行情 + 做T/建仓提示 + 温度仓位。

启动（常驻，随 `run_service.py` 自动拉起）：
```bat
myenv\Scripts\python.exe scripts\run_service.py
```
连通性自检（不发消息）：
```bat
myenv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from invest.push.feishu_ws import check; check()"
```
说明：
- **项目本体直连飞书（WebSocket 长连接，lark-oapi），零 Hermes 依赖**；
- 前置条件（开发者后台 open.feishu.cn/app，应用即群内机器人 Trader-Fox）：
  - 事件与回调 → 事件订阅：订阅方式选**使用长连接接收事件**，订阅**接收消息 im.message.receive_v1**；
  - 权限：`im:message`、`im:message.group_at_msg`、`im:message.p2p_msg` 等；
  - 机器人已加入目标群（`.env` 的 `FEISHU_CHAT_ID`）；
  - **长连接是集群模式**：同一应用同一时刻只有随机一个客户端能收到事件，
    启用本项目前**必须停用 Hermes（或其它工具）对该应用的飞书连接**
    （`powershell -ExecutionPolicy Bypass -File "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem\scripts\disable_hermes_feishu.ps1"`），
    否则消息随机分流 → “艾特机器人经常没回应”（详见 `docs/GATEWAY_STABILITY_ANALYSIS.md`）；
- 非交易时段实时行情不可用，报告自动回退最近收盘数据并明确标注；
- 报告生成复用 `invest/report.intraday_report`，可独立预览：
  `myenv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from invest.report import intraday_report; print(intraday_report('data/invest.db'))"`
- 报告模板（`invest/report.py`，2026-08-15 优化日报；盘中 2026-08-18 精简）：
  - `daily_report`：盘后日报（温度+倾向 / 评级仓位建议 / 板块涨跌 / 短中线强度 / 候选池变化 / 持仓警戒 / Agent 观点）
  - `premarket_report`：盘前清单（评级仓位 / 温度 / 环境重评触发 / Agent 关注方向）
  - `intraday_report`：盘中实时（核心池行情 / 板块异动 / 情绪人气 / 龙虎榜龙头 / 做T建仓提示 / 温度仓位；`public=True` 隐藏持仓警戒）

### 7.2 定时任务调度方式（默认 OS 计划任务模式）

**2026-08-18 起默认 `--ticker-only`**：`run_service.py` 不加参数即 ticker-only——
只跑盘中 10s 轮询 + 飞书接收；定时任务由 Windows 计划任务承担（先运行
`scripts/install_os_tasks.ps1` 注册 9 个任务）。要回到完整 APScheduler 常驻模式
（不装 OS 任务），显式加 `--full`：
```bat
myenv\Scripts\python.exe -u scripts\run_service.py            :: 默认 ticker-only
myenv\Scripts\python.exe -u scripts\run_service.py --full     :: 完整 APScheduler
```
说明：
- 单任务入口 `scripts/run_job.py <job>`（running/ok/failed 留痕 + 失败推送，跑完即退）；
- 注册脚本 `scripts/install_os_tasks.ps1` 用 schtasks /XML 注册 9 个任务
  （StartWhenAvailable=错过补跑、IgnoreNew=不重叠、ExecutionTimeLimit=2h）；
- 盘中 10s 轮询 OS 任务无法表达，必须由 ticker-only 常驻承载；两种模式二选一，勿同时开。

---

## 8. 已知边界与未验证点

- **只辅助决策，不自动交易**；规则先回测后上线，禁止临时加入名单外机会或美化卡片
- 凯利/压力测试/BCS 为代码就绪，`trade_records=0` 无真实交易样本支撑（需一个季度闭环积累）
- 龙虎榜席位分类链路已通，但候选池标的近期无上榜（真实样本待候选池扩充）
- 阶段 1 退出标准未达成：闭环运行一个季度、卡片 ≥20、周复盘无重大违规
- 盘中 4 秒 ticker 真实交易时段行为未实测（周六无法验证）
- 行业 PB 分位代码就绪，数据源（乐咕乐股/东财行业估值）接入后自动生效

---

## 9. 快速上手（新机器）

```bat
cd /d C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem
myenv\Scripts\python.exe -m pip install -r requirements.txt
:: 配置 .env（LLM_API_KEY / TUSHARE_TOKEN / WECOM_WEBHOOK）
myenv\Scripts\python.exe scripts\init_db.py
myenv\Scripts\python.exe scripts\collect.py          :: 首次全量采集
myenv\Scripts\python.exe scripts\backfill.py 20200101  :: 历史回填（可选）
myenv\Scripts\python.exe scripts\run_pipeline.py quant  :: 定量计算
myenv\Scripts\python.exe scripts\run_dashboard.py       :: 看仪表盘
myenv\Scripts\python.exe scripts\run_service.py         :: 常驻调度
```

详细运维见 `docs/OPERATIONS.md`，待办与完成度见 `TODO.md`，手动执行项存档见 `docs/MANUAL_TASKS.md`。
