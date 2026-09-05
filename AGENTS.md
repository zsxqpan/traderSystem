# AGENTS.md — traderSystem 编码约定（供代码生成 agent 每轮参考，减少重踩坑）

> 本文件是本项目对任何代码 agent 的**工程约定声明**：改代码前先读，遵守下列规则。
> 更新：2026-08-21（沉淀自 2026-08-15~20 全部已修问题）。

## 技术栈与结构
- Python 3.10+（本机 3.14）；依赖见 `requirements.txt`；虚拟环境 `myenv/`
- 数据库：SQLite `data/invest.db`，schema 在 `invest/db.py` 的 `SCHEMA_SQL`；**新表必须加进 SCHEMA_SQL 并跑一次 `init_db` 才能在真实库生效**
- 调度：APScheduler（`invest/scheduler.py`）+ OS 计划任务（`scripts/run_job.py` + `scripts/install_os_tasks.ps1`，schtasks /XML）
- 推送：企微 Webhook + 飞书（`invest/push/`）+ 个人微信（iLink）
- 测试：`pytest tests/`，**全 mock 不连真实网络**，临时库用 tempfile；改报告/调度/推送后必须跑相关测试

## 核心约定（必须遵守）
1. **网络请求一律 `trust_env=False`**（绕 WinINET 系统代理 127.0.0.1:7892）；直连优先，勿设 proxies；`urllib` 用 `ProxyHandler({})`
2. **数据库写入用 `invest/data/storage.upsert_df`**（按主键 INSERT OR REPLACE）；写前确保表已建
3. 表主键含 `src` 列允许同一天多源并存（如 daily_bars PK=(symbol,date,src)，snapshot/akshare 可并存）
4. **懒加载**：模块间互相引用的 import 放函数内（invest/pipeline ↔ invest/report 循环依赖）
5. 日期统一 ISO `YYYY-MM-DD`；交易日判断用 `invest/data/calendar`；新浪 datetime 需归一化 `YYYYMMDD` 再比较
6. 日志用 `logging.getLogger(__name__)`；服务是 pythonw 无控制台，靠 `logs/*.log` 文件排查
7. 飞书发文本 `invest/push/feishu_push.send_message`；富文本 `send_post`；表情 `add_reaction`
8. LLM 用量记 `llm_usage`（job 维度）；2026-08-20 起**不拦截只告警**（单次>2万 / 日累计>50万）
9. 数据新鲜度防御：报告/回复前查 `query_data_freshness` / `_data_lag_reason`，滞后先说明原因再答
10. 测试断言用 `pytest`；改定时任务同时更新 `test_pipeline.test_scheduler_jobs` 与 `JOB_FUNCS`

## 已踩过的坑（勿重犯）
- **飞书 lark-oapi**：`Message.mentions[].id` 是 **UserId 对象**（`.open_id` 属性）不是 str/dict；WS 事件用 **v2 信封**（查找键 `p2.xxx`），未订阅事件（如 reaction）须 **p1+p2 双注册 `_ignore_event`** 防 "processor not found" 刷屏
- **akshare 东财日线晚间才更新当日 → 收盘即日线（2026-08-24）**：akshare 走东财 push2his
  kline 接口，本机存在 RemoteDisconnected 且当日更新偏晚；改用**东财 push2delay clist 行情列表接口**
  （`invest/data/close_daily.py::fetch_all_close_daily`）——收盘后立即返回全市场当日 OHLCV
  （f2收盘/f17今开/f15最高/f16最低/f5成交量手/f6成交额，pz=100 分页约 56 页，实测 5207 只 12.7s），
  `snapshot_close` 任务 15:01 写 daily_bars + index_bars(src='snapshot')；晚间 akshare 权威数据写入时
  `collector._drop_snapshot_dups` 删当日 snapshot 行（**daily_bars 和 index_bars 都要清**——
  2026-08-25 曾漏清 index_bars 导致同 (index_code,date) snapshot/akshare 双行，
  quant.strength.calc_rs 的 pd.concat 报 "cannot reindex on an axis with duplicate labels"
  使 after_close/industry_refresh 失败；已加 calc_rs index 去重防御 + 清理历史重复）
- **东财 push2 限流** → 用 `push2delay`（板块资金 clist `f62`）；涨停池 `push2ex getTopicZTPool`（盘中实时）
- 新浪接口 GBK；腾讯指数 `~` 分割 `[2]=代码 [3]=现价`
- **Windows PowerShell 5.1 读 .ps1 需 UTF-8 BOM**（否则中文引号解析崩）
- `run_service.py` **默认 ticker-only**（`--full` 才完整 APScheduler）；单实例用 msvcrt 锁
- **飞书长连接"半开连接"静默失活（2026-08-29 20:27 事故）**：lark-oapi 1.7.2 的 WS 客户端
  对"网络路径静默断掉、出方向仍能发包、入方向收不到帧"无检测——ping 失败只 WARN 不重连、
  recv() 无限阻塞，可数小时收不到任何事件（群 @ + 私聊全丢，飞书不补发），直到服务端
  最终关连接才触发重连（本机实测连接生命周期约 19-20h，05:00/00:23/21:20 均出现过）。
  修复：`feishu_ws` **静默失活看门狗**——帧级钩子（wrap `cli._handle_message`）记录最近收到
  任意帧（含 PONG）的时间，超 `_NO_FRAME_TIMEOUT`（600s）无帧 → `loop.call_soon_threadsafe`
  强制 `cli._disconnect()` 触发 SDK 自动重连；健康连接下服务器约每 120s 回 PONG，安静不误触发。
  **依赖私有 API**（`_handle_message`/`_disconnect`/模块级 `loop`），lark-oapi 升级需回归
  `tests/test_feishu_ws.py` 看门狗用例；挂载失败自动降级（看门狗不可用，不影响收发）。
- Python 语法：关键字参数位置不能裸 walrus（`user=(x := ...)` 需括号）；try/with/finally 配对别写错
- 涨停判断：主板 ≥9.8%、20cm 板 ≥19.8%；两市成交额=上证+深成指，别加创业板/科创50（子集重复）

## 模块地图（改哪里找哪里）
- `invest/data/`：采集（collector / realtime 三源 / emotion 涨停池 / fund_flow 板块资金 / global_snapshot 隔夜外围 / sources / storage / calendar）
- `invest/quant/`：量化计算（strength/rotation/temperature/capital/linkage/valuation/emotion_cycle/alpha158）
- `invest/report.py`：日报/周报/盘中报告（`brief` 简洁版默认，`public` 去持仓警戒）/消息面（LLM 提炼）
- `invest/agent/`：LLM 客户端（llm.py）+ 工具注册表（tools.py：**新增工具必须同步加 TOOL_SCHEMAS 和 dispatch**）+ 双 Agent（agents.py）
- `invest/push/`：飞书（feishu_ws 长连接接收 / feishu_push 发送）/ 微信（weixin_push）
- `invest/scheduler.py`：全部定时任务 + `JOB_FUNCS`（OS 任务入口）+ ticker
- `scripts/`：run_service（常驻）/ run_job（单任务）/ install_os_tasks（计划任务注册）/ start_service

## 静态检查（2026-08-21 引入）
- `ruff check invest scripts tests`：**必须 0 错误**（配置见 ruff.toml；忽略项为项目约定：DTZ 本地时间 / S110 静默容错 / BLE001 宽异常）
- `mypy invest/`：渐进式注解辅助检查，当前有存量类型债（见 TODO 改进清单），不作为硬闸门
- 改代码后：先跑相关 pytest，再跑 ruff；新依赖加进 requirements.txt

## Agent 联网与 Skill 流水线（2026-08-21）
- `invest/agent/web_tools.py`：`web_search`（必应 cn，无 key）+ `web_fetch`（抓正文）——飞书 Agent 查最新资讯/财报/新闻用；均已注册进 TOOL_SCHEMAS
- `invest/agent/skill_runner.py`：`run_skill` 跑 **UZI deep-analysis** 完整流水线（subprocess 调 `tools/hermes_skills/UZI-skill/skills/deep-analysis/run.py`，注入 DeepSeek OpenAI 兼容凭据 OPENAI_API_KEY/BASE_URL，`--no-browser --depth lite/medium/deep`，输出 HTML 报告路径+摘要）
- serenity/youzi/stock-analysis 为方法论文档（无脚本），由 CHAT_SYSTEM 注入方法论
- 新增工具必须：tools.py 的 TOOL_SCHEMAS + `_IMPLEMENTATIONS` 同步注册

## 通用工程 Skill（2026-08-21 装于 .claude/skills/）
- `brainstorming/`：头脑风暴→设计文档（obra/superpowers，输出到 docs/superpowers/specs/）
- `grill-me/` + `grilling/`：拷问式需求对齐（mattpocock/skills，grill-me 是调用 grilling 的壳）
- `systemdebugging/`：系统化 Bug 诊断循环（mattpocock diagnosing-bugs：minimise→hypothesis→instrument→fix→regression）
- 均为 Claude Code/agent 通用 skill（SKILL.md 方法论），与金融分析 skill（tools/hermes_skills）无冲突

## 轻量角度分析 Skill（2026-08-23 拆分自 UZI · 日常对话快答）
- 9 个 SKILL.md 装于 `.claude/skills/`：`angle-selector`（主角度判别）/ `stock-emotion`（情绪面）/
  `stock-technical`（技术面）/ `stock-fundamental`（基本面）/ `stock-cycle`（周期，情绪+行业双尺度）/
  `trap-scan`（杀猪盘 8 信号）/ `sector-analysis`（板块）/ `opinion-analysis`（舆情，雪球/股吧/新闻）/
  `big-v-monitor`（雪球大V 画像与观点，读写 big_v_profile/big_v_opinion 表）
- 设计文档：`docs/superpowers/specs/2026-08-23-UZI拆分轻量子skill-design.md`
- 触发：CHAT_SYSTEM 内置角度 skill 触发词表（~500 字），模型按语义自选，回复末尾标注
  `↘ 已使用 Skill：xxx`；主角度判别规则见 `angle-selector`（连板→情绪、周期股→周期、破净→基本面等）
- 数据依赖：本地工具（query_stock_daily / cross_validate / query_temperature / query_macro /
  query_rotation / query_capital / web_search / web_fetch / query_big_v / big_v_update）；
  深度报告仍走 `run_skill`（UZI deep-analysis），两者分工：日常快答 vs 报告级
- 报告侧复用（2026-08-23 已落地，invest/skills/sections/）：`d28_community_hot`（社区热议→日报）/
  `d29_sector_resonance`（板块共振→日报+盘中，纯规则）/ `d30_cycle_position`（周期行业定位→周报，纯规则）/
  `d31_pool_trap_alerts`（候选池杀猪盘 8 信号扫描→17:10 定时任务，写 pool_trap_alerts 表+≥🟡推送飞书）；
  设计文档：`docs/superpowers/specs/2026-08-23-报告类复用角度skill接入-design.md`
- **对话侧复用 D 组小节（2026-08-23）**：`run_section` 工具把 31 个报告小节 skill（d1-d31）暴露给
  日常对话——模型按语义调用现成分析文本（情绪/连板/板块/资金/宏观/外围/周期等），与角度 skill 互补；
  实现：tools.py `run_section`（自动注入 db_path、透传参数、失败返回 error）；CHAT_SYSTEM 规则 5b 注入常用清单
- **数据新鲜度硬门禁（2026-08-23 修订 2026-08-24/25）**：飞书对话（feishu_ws._agent_chat）启用 `set_freshness_gate(True)`；
  数据类工具守卫**收窄到个股/行业行情核心**（_DATA_TOOLS：query_stock_daily/cross_validate）——
  query_temperature（涨停池独立采集）/query_strength 等 quant/query_macro（宏观低频）/query_pool（候选池）
  **不受 daily_bars 守卫约束**；非交易时段（含盘前）日线/指数任一到最近交易日即放行，都缺才拦截；
  `query_data_freshness` 的 fresh 只看日线/指数，**quant 衍生指标滞后（quant_stale）不阻塞行情回答**；
  **run_section 分级守卫**：仅**直接读 daily_bars** 的小节（d16 持仓警戒/d18 异常波动/d19 做T/d31 杀猪盘K线）
  过守卫；其余 27 个小节（消息/舆情/宏观/情绪/连板/资金/龙虎榜/板块/quant 等，数据源为独立采集表
  或联网或 quant）**跳过守卫**（2026-08-25 全面排查，修 d27 消息汇总/d11 情绪/温度等被误伤）；
  30s 缓存；脚本/测试默认关闭不受影响（feishu_ws 对话结束 finally 复位）
- **报告四项修复（2026-08-24）**：① b1 移除 index_bars 图表（盘面总览表格已含全部指数涨跌幅，避免重复输出）；
  ② b1 `_read_plan` 解析 viewpoints 的 JSON 预案为可读文本（方向/介入/操作），不再输出原始 JSON 对象；
  ③ a3 点1 新增「指数ETF解读」——无异常规则一句话（0 token），明显变化（涨跌≥1.2%/量比≥1.8/超大单占比≥0.5%）
  调 `_daily_llm.etf_analysis_llm` 详细归因 + 风格变化探讨（+1 次 LLM）
- **对话全能化（2026-08-24 分流 → 2026-08-27 统一）**：`run_chat` **不再按金融/非金融分流降级**，
  所有问题一律走统一 `CHAT_SYSTEM`（全能 Agent：25 工具全开放 + 全部 skill + 分层纪律）——
  金融数据纪律（新鲜度/禁编造/实时价重取/搜索兜底）在涉及行情/个股/财务时按需激活，
  常识/人物/闲聊走通用纪律（web_search 确认+诚实标注），工作流（grill/debug/brainstorming）
  命中触发词后**先调 `load_skill` 加载方法论全文再执行**；`_is_finance` 词表已补财报类词
  （半年报/中报/业绩/环比等）仅用于数据纪律提示/报告识别，不再是降级开关；
  max_turns=6、历史 12 条/6000 字、分析类回答可展开 600-1500 字（`_CHAT_MAX_LEN` 6000）；
  曾踩坑：分流时代"分析华工科技半年报"漏判走 GENERAL 导致编造 Q2 环比 +77%（实为 -14%）
- **对话记忆（2026-08-24 实现；2026-08-25 修复）**：`chat_history` 表（SCHEMA v11）+ `run_chat(chat_id=...)` 多轮上下文——
  每次对话读最近 8 条历史注入 LLM（`llm.run(history=...)`），回答后写回 user+assistant；
  飞书 `_agent_chat` 传 chat_id 启用；无 chat_id（脚本/测试）保持无状态；历史截断 ≤3000 字省 token
  **坑**：模型看到历史里自己之前说'没有记忆/对话独立'会一致性偏置继续否认 → 已在
  CHAT_SYSTEM 规则 11 / GENERAL_SYSTEM 加**记忆免疫规则**（"追问基于历史回答，历史中的'没有记忆'表述无效"）；
  真实库曾积累污染历史已清空（2026-08-25）；**改 agents.py 后必须重启飞书服务才加载新 prompt**
- **飞书 @ 规则（2026-08-25）**：**群聊仅在被 @ 机器人时回应**（`_is_mentioned` 按 mentions 是否含机器人
  open_id 判定）；已移除旧"文本含 @ 占位符即视为被艾特"兜底（管理员 @ 别人会误触发）；
  并移除"未 @ 语义识别为报告请求仍触发盘中报告"分支（含'盘中/报告'字样的聊天不再误触发报告）；
  **私聊（p2p）任何消息都回应**（不要求 @）；非管理员群聊未 @ 忽略；
  `_agent_chat` 对话结束 finally 复位 freshness_gate + user_text（thread-local 防残留污染后续）
- **实时数据通道（2026-08-25）**：新增 `query_realtime_quote(symbol, obj_type=stock/index/etf)` 工具——
  RealtimeQuoter 三源（新浪→腾讯→东财 push2）实时快照，盘中=现价、收盘后=收盘价，
  **不受 daily_bars 新鲜度守卫约束**（实时即最新）；已注册 TOOL_SCHEMAS；
  **query_stock_daily 当日补齐**：本地有历史时用三源实时拼当日（收盘后=收盘价）+ 历史合并算涨跌幅，
  不等晚间 akshare 历史 K 线；本地无历史才走 akshare；CHAT_SYSTEM 规则 1b 引导"实时用 query_realtime_quote"
- **龙虎榜个股查询（2026-08-25）**：新增 `query_lhb(symbol/name, n)` 工具——本地 dragon_tiger 表
  按股票查最近 N 次（日期/买卖/净额，过滤 seat_type NULL 历史重复行）；问"XX 的龙虎榜"直接查本地，
  不再依赖 web_search（必应中文分词会把公司名拆成单字词典词条）；
  CHAT_SYSTEM 规则 1 加**搜索重试引导**（结果变词典词条时换关键词：加代码/全称/限定词再试）
- **web_search 多引擎降级（2026-08-25）**：**DeepSeek 官方联网搜索优先**（Anthropic 兼容
  `/anthropic/v1/messages` + `web_search_20250305` 原生工具，服务端搜索返回结构化
  web_search_tool_result + citations 摘要；**每次搜索=一次模型调用（耗 token）**；未配置 key/
  失败/未触发 → 降级）→ 降级引擎链 必应cn → 搜狗 → 360 → 百度（百度对无浏览器 requests 直接返回
  "安全验证"验证码页，已识别快速跳过放最后；搜狗/360 稳定）+ 质量检测（含 6 位代码必须命中；
  排除词典 URL 且标题须含查询词 2 字连续片段）+ snippet 摘要；首个质量达标返回；site: 强制必应；
  实测官方搜索精确命中"孚日股份(002083)龙虎榜数据 东方财富""连续3天涨停"等；
  CHAT_SYSTEM 规则 1/3b：本地查不到必须自动 web_search 兜底、结果无直接答案换词再搜、
  搜到专题页 web_fetch 打开、禁止不调工具断言"本地没有/数据滞后"
- **DSH skill 目录（2026-08-25）**：DSH（DeepSeek Harness）的 skill 工具**只扫 `.dsh/skills` /
  `.agents/skills` / `$DSH_HOME/skills`，不扫 `.claude/skills`**——工作区 skill（brainstorming/
  grilling/systemdebugging + 9 角度 skill）已**复制到 `.dsh/skills/`**（hot-refresh 即时生效）；
  新增/修改 .claude/skills 下的 skill 时记得同步复制到 .dsh/skills（或直接放 .dsh/skills）
- **雪球信息渠道（2026-08-24 实测；2026-08-25 Playwright 落地）**：雪球站内 API 与页面正文被**阿里云 WAF**
  硬挡（requests 抓不了）；搜索引擎只能拿标题+摘要（xueqiu_search）；**Playwright 无头 chromium +
  反检测（真实 UA / 禁 automation 标志 / navigator.webdriver 置 undefined / 先访问首页过 WAF 挑战）
  可成功抓取正文**（已验证：段永平文章全文）——`invest/data/xueqiu_fetch.py`（fetch_article /
  fetch_user_statuses）+ 工具 `xueqiu_fetch_article(url)` / `xueqiu_fetch_user(user_id)`（对话按需抓取、
  big_v_opinion 按 url 去重入库、fetch_user 自动建 profile）；
  **依赖**：playwright + chromium（npmmirror 镜像装，%LOCALAPPDATA%\ms-playwright）；每次调用独立
  启动/关闭浏览器串行执行；spec：`docs/superpowers/specs/2026-08-25-雪球playwright采集-design.md`；
  二期：登录态抓需登录字段 / 社区热帖（d28 升级）/ 定时批量采集
