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
- **akshare 东财日线晚间才更新当日** → 当日收盘价用实时快照（三源 / 腾讯指数 `qt.gtimg.cn`，16:10 `snapshot_close` 任务）
- **东财 push2 限流** → 用 `push2delay`（板块资金 clist `f62`）；涨停池 `push2ex getTopicZTPool`（盘中实时）
- 新浪接口 GBK；腾讯指数 `~` 分割 `[2]=代码 [3]=现价`
- **Windows PowerShell 5.1 读 .ps1 需 UTF-8 BOM**（否则中文引号解析崩）
- `run_service.py` **默认 ticker-only**（`--full` 才完整 APScheduler）；单实例用 msvcrt 锁
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
