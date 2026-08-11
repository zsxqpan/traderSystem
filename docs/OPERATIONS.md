# A股投资系统 · 使用与运维手册

> 版本：2026-08-04 | 仓库：traderSystem | 环境：Windows 本地 + Python 3.14（myenv 虚拟环境）

---

## 1. 系统概览

```
数据采集 → 定量计算 → 回测校准 → 观点库 → 执行纪律 → Agent 推理 → 调度推送 → 仪表盘/API
```

- 全部代码在仓库内，数据库为 `data/invest.db`（SQLite）。
- 推送：企业微信机器人（盘前清单 / 盘后日报 / 周末周报 / 盘中异动 / 任务失败告警）。
- 边界：**只辅助决策，不自动交易**。

---

## 2. 环境与配置

### 2.1 虚拟环境
```cmd
cd /d C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem
myenv\Scripts\python.exe -m pip install -r requirements.txt   # 已装核心，可补装
```

### 2.2 .env（密钥与参数）
文件：`.env`（已 gitignore）。必填/常用项：

```ini
LLM_API_KEY=你的DeepSeek密钥
TUSHARE_TOKEN=你的Tushare token      # 备用源，可选
WECOM_WEBHOOK=你的企业微信机器人地址   # 推送，可选
DB_PATH=data/invest.db
DAILY_LLM_BUDGET_TOKENS=60000        # 每日 LLM 预算
RISK_MAX_DRAWDOWN=0.15               # 风控默认参数
```

### 2.3 config.yaml（结构性参数）
- `rating_position_map`：评级-仓位映射（2026-08-04 已按回测校准）
- `indicators`：量化指标参数（待进一步回测）
- `data`：主复权口径（新浪 qfq）、行业清单说明

---

## 3. 常用命令（仓库根目录执行）

| 目的 | 命令 |
|---|---|
| 数据采集（含行业全量/情绪/估值） | `myenv\Scripts\python.exe scripts\run_pipeline.py collect` |
| 定量计算 | `myenv\Scripts\python.exe scripts\run_pipeline.py quant` |
| 盘前全流程 | `myenv\Scripts\python.exe scripts\run_pipeline.py premarket` |
| 盘后全流程（含自动仲裁） | `myenv\Scripts\python.exe scripts\run_pipeline.py after_close` |
| 周末周报 | `myenv\Scripts\python.exe scripts\run_pipeline.py weekend` |
| 回测（4 类规则） | `myenv\Scripts\python.exe scripts\run_backtest.py` |
| 复盘（周/月/年） | `myenv\Scripts\python.exe scripts\run_review.py weekly`（monthly/yearly） |
| 仪表盘 | `myenv\Scripts\python.exe scripts\run_dashboard.py` → http://localhost:8501 |
| API 服务 | `myenv\Scripts\python.exe scripts\run_api.py` → http://127.0.0.1:8000/docs |
| 常驻调度服务 | `myenv\Scripts\python.exe scripts\run_service.py` |
| 候选池/评级/计划 CLI | `myenv\Scripts\python.exe scripts\discipline.py pool add 600519 --level core --industry 白酒` |
| 情绪历史回填 | `myenv\Scripts\python.exe scripts\backfill_emotion.py 60` |
| 估值历史回填 | `myenv\Scripts\python.exe scripts\backfill_valuation.py 5` |
| 行情历史回填 | `myenv\Scripts\python.exe scripts\backfill.py 20200101` |
| 盘中异动检查 | `myenv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); import invest.intraday as i; print(i.check_core_moves('data/invest.db'))"` |

---

## 4. 调度与开机自启

### 4.1 调度任务（run_service.py 常驻）

| 时间 | 任务 |
|---|---|
| 工作日 08:30 | 采集 + 定量 + 投研清单 + 盘前推送 |
| 工作日 09:35-11:30 / 13:05-14:55 每 5 分钟 | 核心关注盘中异动监测（3% 阈值，首条附 LLM 归因，同标的 5 分钟限频） |
| 工作日 16:00 | 全量采集 + 定量 + 交易复盘 + 自动仲裁 + 盘后日报 |
| 周六 09:00 | 周报 + 周度纪律复盘 |
| 每月 1 日 09:30 | 月度观点复盘 |
| 每年 1 月 1 日 09:30 | 年度规则复盘 |
| 工作日 21:30 | 行业指数当天数据刷新（同花顺晚间发布）+ 定量重算 |\n| 每日 22:00 | 每日复盘推送（板块涨幅/强度为**当天**数据）+ 观点到期入队 / 工单超时检查 |

### 4.2 开机自启（已注册）
- 注册位置：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` → `InvestSystemService`
- 验证：`reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v InvestSystemService`
- 移除：`myenv\Scripts\python.exe scripts\remove_autostart.py`
- 立即手动启动：`myenv\Scripts\python.exe scripts\run_service.py`（保持窗口开着）

---

### 4.3 休眠也能准点推送（唤醒定时器）
- 系统已注册计划任务 `InvestSystemWake`：工作日 08:25 / 15:55、每天 21:58 唤醒电脑（WakeToRun）并守护调度服务（`scripts/wake_guard.ps1`，服务不在则自动拉起），分别保障 08:30 盘前、16:00 盘后、22:00 复盘推送不因睡眠/休眠错过。
- 电源"允许使用唤醒定时器"已设为启用（交流+直流）。若不想电池模式下唤醒，可改回禁用：
  `powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 0 && powercfg /setactive SCHEME_CURRENT`
- 例行任务已设错失宽限（misfire_grace_time）：盘前 6h / 盘后 6h / 夜间 2h / 周末 2h / 月度和年度 12h。机器在触发后宽限窗口内醒来会补跑；关机或断电仍无法发送（需云端方案）。

---

## 5. 企业微信推送

- 未配置 `WECOM_WEBHOOK` 时推送自动禁用，不影响其他功能。
- 限频：盘前/盘后 10 分钟合并；盘中同标的 5 分钟；任务失败告警即时。
- 消息类型：盘前清单 / 盘后日报 / 周末周报 / 盘中异动（含归因）/ 月度复盘 / 年度复盘 / 任务失败。

---

## 6. 数据库

- 文件：`data/invest.db`（WAL 模式，备份时注意 `-wal`/`-shm` 文件）。
- 备份（推荐在线备份）：
```cmd
myenv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/invest.db'); c.execute(\"VACUUM INTO 'backup_invest.db'\"); c.close()"
```
- 常用表：`daily_bars/index_bars/industry_bars`（行情）、`market_emotion`（情绪）、`industry_valuation`（PE 历史）、`quant_*`（定量结果）、`viewpoints`（观点）、`tickets`（工单）、`candidate_pool/ratings/trade_plans/trade_records/risk_rules`（纪律）、`job_runs/llm_usage/backtest_runs/review_reports`（运行与复盘）。

---

## 7. 常见问题排查

| 现象 | 处理 |
|---|---|
| 任务失败 | 查 `job_runs` 表最新记录；失败会自动推送企业微信 |
| 数据源不可达 | 适配器已做多级降级：东财↔新浪、push2ex/push2his 主机轮询、同花顺行业；个别源仍失败会降级标记 |
| 代理限流（连接被断） | 等 1-2 分钟重跑；主机轮询已尽量规避 |
| LLM 超预算/跳过 | 查 `llm_usage` 表；调大 `.env` 的 `DAILY_LLM_BUDGET_TOKENS` |
| 观点到期提醒 | 夜间任务自动把到期观点转 `pending_review`，复盘时处理 |
| 控制台中文乱码 | 仅显示问题（GBK 终端），数据本身正确；用仪表盘/API 查看即可 |
| 指标/规则疑似不准 | 先跑 `scripts\run_backtest.py` 看回测结论，规则参数在 config.yaml 调整 |

---

## 8. 数据源可用性备忘（2026-08-04 实测）

| 数据 | 主源 | 备注 |
|---|---|---|
| 个股/指数日线 | 东财 → 新浪回退 | 主复权口径=新浪 qfq |
| 行业指数 | 同花顺（映射缓存） | 全量行业 |
| 宏观（PMI/货币/信贷） | 东财 datacenter | 已回填 2008 至今 |
| 两融 | 上交所 | 你本机可达 |
| 龙虎榜/席位 | 东财 datacenter | 席位明细仅候选池个股、近 5 日 |
| 涨停/炸板 | 东财 push2ex | 主机轮询；炸板池仅近 30 天 |
| 行业 PE | 巨潮 cninfo | **待你本机验证可达性** |
| 盘中分钟线 | 新浪 quotes | **待你本机验证可达性** |
| 社融增量 | 东财新增信贷替代 | 商务部源 TLS 坏 |

---

## 9. 安全与合规

- 系统不接入任何自动交易通道。
- 密钥只存 `.env`（已 gitignore），勿提交到 git。
- 免费数据源按各自条款使用，采集已做限速；请勿高频抓取。
- API 仅监听 127.0.0.1，如需局域网访问请自行加认证。