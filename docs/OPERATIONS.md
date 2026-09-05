# A股投资系统 · 使用与运维手册

> 版本：2026-08-28 | 配合 `docs/SYSTEM_GUIDE.md`（介绍与日常用法）
> 环境：Windows 本地 + Python 3.14（`myenv` / `.venv`）

---

## 1. 概览

```
采集 → 定量 → 纪律/卡片 → Agent → 调度送达 → 飞书/企微/微信 → 仪表盘比价
```

- 库：`data/invest.db`（SQLite WAL）。
- 默认部署：`run_service.py` **ticker-only**（10s 行情 + 1min 补偿 + 飞书）。13 个例行任务走 OS 计划任务。
- **只辅助决策，不自动交易。**

日常「怎么问飞书、怎么比价」见系统介绍 §2。本文只写安装、送达、备份、排障。

---

## 2. 环境

```cmd
cd /d C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem
myenv\Scripts\python.exe -m pip install -r requirements.txt
myenv\Scripts\python.exe scripts\init_db.py
```

升级代码后若有新表（`job_executions`、`delivery_receipts`、`fact_cards` 等），**必须再跑一次 `init_db`**，否则真实库不会建表。

`.env` 常用项：`LLM_API_KEY`、`TUSHARE_TOKEN`、`WECOM_WEBHOOK`、`FEISHU_*`、`DB_PATH`。勿提交 git。

---

## 3. 常用命令

| 目的 | 命令 |
|---|---|
| 常驻（默认） | `myenv\Scripts\python.exe scripts\run_service.py` |
| 完整 APScheduler | `scripts\run_service.py --full`（勿与 OS 任务同时开） |
| 注册 13 个 OS 任务 | `powershell -File scripts\install_os_tasks.ps1` |
| 单任务 | `scripts\run_job.py auction`（见 `JOB_FUNCS`） |
| 采集 / 定量 / 盘前 / 盘后 | `scripts\run_pipeline.py collect`（quant / premarket / after_close） |
| 仪表盘 | `scripts\run_dashboard.py` |
| 复盘 | `scripts\run_review.py weekly` |
| 回填 | `scripts\backfill.py 20200101` |
| 评测集 | `python -m pytest tests/test_eval_e2e.py` |

`run_job.py`：仅 `ok` / `already_ok` 退出码 0；同槽占用超时退出 **75**（deferred）；失败非 0。Windows 任务计划看到 75 表示另一实例还在跑，不是业务一定失败。

---

## 4. 调度与补偿

### 4.1 任务表

与 `JOB_FUNCS` / `install_os_tasks.ps1` 一致：

`premarket` 08:30 · `morning_brief` 08:40 · `auction` 09:26 · `snapshot_close` 15:01 · `after_close` 16:00（不推日报）· `pool_trap_scan` 17:10 · `industry_refresh` 21:30 · `daily_refresh` 21:40 · `factcard_refresh` 21:50 · `evening_report` **每日 22:00** · `weekend` 周日 20:00 · `monthly` 每月 1 日 09:30 · `yearly` 每年 1 月 1 日 09:30。

盘中 10s ticker **只能**由常驻服务跑。

晚报：OS / `--full` 都是每天 22:00 触发；**补偿扫描按交易日**，周末、节假日不补发。

### 4.2 补偿扫描

常驻服务每分钟扫「应跑且未成功」的槽位：

- 仍在业务窗口内 → 走正常 `_execute_job`（成功通道不重发）
- 已过窗 → 只记 `missed` 并告警
- 竞价过窗 **绝不**用盘中行情补一份「竞价报告」

### 4.3 开机与唤醒

- 自启：`HKCU\...\Run\InvestSystemService`；去掉用 `scripts\remove_autostart.py`
- 唤醒：`InvestSystemWake`（工作日 08:25 / 15:55、每天 21:58）+ `wake_guard.ps1`
- 关机断电无法补发，只能次日告警

---

## 5. 送达账本（排障第一眼）

```sql
-- 今天各任务终态
SELECT job, scheduled_date, run_slot, status, attempt, detail
FROM job_executions
WHERE scheduled_date = date('now','localtime')
ORDER BY job;

-- 某次投递各通道
SELECT message_kind, message_id, channel, status, updated_at
FROM delivery_receipts
WHERE scheduled_date = date('now','localtime')
ORDER BY updated_at DESC;
```

状态含义：`ok` 成功；`failed` 业务或发送失败（窗口内可重试）；`missed` 过窗未成功；`running` 有租约，崩溃后超时可回收；投递 `uncertain`（超时/断连）**不自动重发**，需人工核验通道是否已收到。

飞书盘中报告回执按会话区分（`run_slot` 含 `chat_id`），同一分钟不同群/私聊不会互相吞。

---

## 6. 证据编号

推送或比价页上的 `EVID-YYYYMMDD-0001`：

- 飞书：把编号发给机器人
- 仪表盘中期比价：检索框
- SQL：`SELECT * FROM fact_evidence WHERE evidence_id = 'EVID-...'`

无引擎/页面发布日的搜索结果不会进近 3–7 日新闻，卡上可能出现 `missing: news`。

---

## 7. 推送

- 未配 webhook / 飞书凭据则该通道关闭。
- 盘中异动同标的 30 分钟；报告任务走逐通道回执。
- 飞书：群聊仅 @ 回应；私聊都回；限频有文字反馈。
- 类型：盘前 / 竞价 / 晚报 / 周报 / 异动 / 事实卡变化 / 任务失败。

---

## 8. 备份

```cmd
myenv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/invest.db'); c.execute(\"VACUUM INTO 'backup_invest.db'\"); c.close()"
```

WAL 模式下同时留意 `-wal` / `-shm`。

---

## 9. 排障

| 现象 | 处理 |
|---|---|
| 竞价没收到 | 确认 `TraderSystem_auction` 09:26；过窗只有 missed 告警，不会补发 |
| 晚报没收到 | 看 21:40 `daily_refresh` 是否把日线补到最近交易日；滞后只推原因 |
| 盘中报告有的票没价 | 看表上「状态」列（源失败/停牌/缺历史），不是静默丢行 |
| 飞书群没反应 | 是否 @ 了机器人；是否被 120s/10s 限频；是否 Hermes 抢了长连接 |
| 问现价却出了盘中报告 | 用「茅台现价」；「现在行情怎么样」+ 股票名应走报价，明确「盘中报告」才出报告 |
| 任务失败 / 漏跑 | `job_executions` + `delivery_receipts`；窗口内等补偿，过窗看告警 |
| 代理连不上 | 代码 `trust_env=False` 直连，勿走 127.0.0.1:7892 |
| LLM 超用量 | `llm_usage`；单次>2万 / 日>50万只告警不拦截 |
| 控制台中文乱码 | GBK 终端显示问题，看仪表盘或日志文件 |

---

## 10. 数据源备忘

| 数据 | 主源 |
|---|---|
| 日线 | 15:01 snapshot（push2delay clist）；晚间 akshare |
| 实时 | 新浪 → 腾讯 → 东财，按标的 merge |
| 行业指数 | 同花顺 |
| 宏观 | 东财 + 社融接口 |
| 涨停池 | push2ex getTopicZTPool |
| 龙虎榜 | 东财 datacenter |

盘中分钟线只做历史回填，**不得当实时兜底**。

---

## 11. 安全

- 无自动交易通道。
- 密钥只在 `.env`。
- API 默认 127.0.0.1。
