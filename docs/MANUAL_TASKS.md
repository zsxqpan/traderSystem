# 用户手动执行清单（TODO [B] 类 8 项）

> 2026-08-15 整理 | 所有命令在项目根目录执行：
> `cd /d C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem`
> Python 一律用虚拟环境：`myenv\Scripts\python.exe`
> 每项做完回报结果（贴输出），我来核对并勾选 TODO。

## 进度（2026-08-15）
- ✅ 1. Tushare token 配置（rows=149 验证通过）
- ✅ 2. 社融增量数据源接入（商务部源 macro_china_shrzgm，quant_macro 输出社融增量）
- ✅ 3. 行业全量首跑验证（industry_all=57060 行，行业数 90）
- ✅ 4. 龙虎榜席位类型（链路打通 + 修复 2 bug；真实样本待候选池扩充）
- ✅ 5. 数据回填执行（个股/指数/行业最早均到 2020-01-02）
- ✅ 6. 本机跑 pytest 全量（EXITCODE=0）
- ✅ 8. 环境重评触发条件落地（ERP 跨分位/社融拐点/10Y>20bp，真实触发验证通过）
- ⏳ 7. FastAPI 接口层（按需；代码已就绪，需要时启动 `scripts/run_api.py`）

以下保留原始步骤存档。

---

## 1. Tushare token 配置 → 备用源 + 日线交叉校验

**目的**：日线获取失败时自动用 Tushare 兜底；关键数据交叉校验（已接好，只差 token）。

步骤：
1. 打开 https://tushare.pro 注册登录。
2. 右上角「个人主页」→ 复制你的 token（一串 32 位字符串）。
3. 编辑项目根目录 `.env`，找到 `TUSHARE_TOKEN=` 行，填入 token：
   ```
   TUSHARE_TOKEN=你的token
   ```
   （没有这行就手动加；用记事本编辑即可）
4. 验证 token 可用（应打印出 000001 的日线行数 > 0）：
   ```bat
   myenv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from invest.data.sources.tushare_source import TushareSource; import os; from invest.config import get_settings; s=TushareSource(get_settings().tushare_token); df=s.fetch({'kind':'daily_bars','symbol':'000001','start_date':'20260101','end_date':'20260815'}); print('rows=', len(df)); print(df.head(2))"
   ```
5. 预期输出：`rows= 150+` 且打印出两行日线（若报"积分不足"说明 tushare 账号积分不够 daily 接口，需 120 积分以上）。

**回报**：第 4 步的输出（成功 / 报错原文）。

---

## 2. 社融增量数据源接入（确认东财 reportName）

**目的**：把宏观指标从「新增信贷替代」换成「真实社融增量」。

步骤：
1. 先探测东财 datacenter 的社融增量接口是否可达（浏览器直接打开下面链接）：
   ```
   https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_ECONOMY_SHFINANCING&columns=ALL&pageNumber=1&pageSize=5&sortColumns=REPORT_DATE&sortTypes=-1
   ```
2. 预期：页面返回 JSON，`result.data` 有 5 条社融数据（含 `REPORT_DATE`、`SHFZJE` 等字段）。请把**字段名**（前几条的 key 列表）贴给我。
3. 若链接打不开或报错，试试东财新增信贷接口是否仍可用（现状代码用的）：
   ```
   https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_ECONOMY_NEW_FINANCIAL_CREDIT&columns=ALL&pageNumber=1&pageSize=5
   ```

**回报**：第 1 步链接的字段名列表 + 是否成功。拿到后我改 `invest/data/sources/akshare_source.py` 的 `_fetch_macro` 接入真实社融。

---

## 3. 行业全量首跑验证（本机 collect）

**目的**：验证本机网络能拉全 80+ 行业日线（此前只验证过部分行业）。

步骤：
1. 跑一次完整采集（含行业全量，约 1-3 分钟）：
   ```bat
   myenv\Scripts\python.exe scripts\collect.py
   ```
2. 预期输出：`industry_all  ok  [akshare=xxxx行]`（其他任务失败可忽略，只看 industry_all）。
3. 核对行业数量（预期 80+）：
   ```bat
   myenv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/invest.db'); print('行业数 =', c.execute('SELECT COUNT(DISTINCT industry) FROM industry_bars').fetchone()[0])"
   ```

**回报**：第 1 步中 industry_all 那行 + 第 3 步的行业数。

---

## 4. 龙虎榜席位类型（机构/游资/量化）

**目的**：验证席位明细能拉到，让 `quant_capital.fund_type` 出真实值（分类代码已写好，缺真实数据）。

步骤：
1. 确保候选池至少有一个标的（已有 000001）。跑采集（含 seat_detail）：
   ```bat
   myenv\Scripts\python.exe scripts\run_pipeline.py collect
   ```
2. 查询席位明细是否入库（预期有行；若 000001 近期无龙虎榜可能为 0 行）：
   ```bat
   myenv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/invest.db'); rows=c.execute('SELECT COUNT(*) n, COUNT(DISTINCT symbol) s FROM dragon_tiger').fetchone(); print('dragon_tiger 总行数 =', rows[0], '| 涉及标的 =', rows[1])"
   ```
3. 若有席位数据，查资金类型分类结果：
   ```bat
   myenv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/invest.db'); [print(r[0], r[1], r[2]) for r in c.execute('SELECT symbol, fund_type, confidence FROM quant_capital WHERE fund_type IS NOT NULL LIMIT 10')]"
   ```

**回报**：第 2 步行数 + 第 3 步输出（若为 0 行说明标的近期无上榜，属正常，不用处理）。

---

## 5. 数据回填执行（起点 2020-01-01）

**目的**：把个股/沪深300/行业日线前推回 2020，供 3-5 年分位与季度 OOS 评估使用。

步骤：
1. 执行回填（约 3-6 分钟，窗口开着别关）：
   ```bat
   myenv\Scripts\python.exe scripts\backfill.py 20200101
   ```
2. 预期输出：每个任务一行 `ok`；最后统计成功数。
3. 核对最新/最早日期：
   ```bat
   myenv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/invest.db'); print('daily_bars 最早 =', c.execute('SELECT MIN(date) FROM daily_bars').fetchone()[0]); print('industry_bars 最早 =', c.execute('SELECT MIN(date) FROM industry_bars').fetchone()[0])"
   ```
   （个股最早应为 2020-01-02 前后；行业若回填失败则保持 2024 起点，不影响 PE 分位主价差）

**回报**：第 2 步成功统计 + 第 3 步两个日期。

---

## 6. 本机跑 pytest 全量

**目的**：在真实网络环境跑全部测试（含依赖外部 key/网络的 test_data / test_agent / test_api，沙箱里没跑过）。

步骤：
1. 执行全量测试（约 1-2 分钟）：
   ```bat
   myenv\Scripts\python.exe -m pytest tests/ -q
   ```
2. 预期输出：`140 passed` 以上（目前沙箱内 153 passed；本机多出 test_data/test_agent/test_api 的真实网络用例，可能多几个）。

**回报**：末尾的 `xxx passed / xxx failed` 汇总行 + 失败的测试名（若有）。

---

## 7. FastAPI 接口层（按需）

**目的**：仪表盘之外需要 API 时启用（代码已就绪，仅启动验证）。

步骤：
1. 启动服务（窗口保持打开）：
   ```bat
   myenv\Scripts\python.exe scripts\run_api.py
   ```
2. 浏览器打开 `http://127.0.0.1:8000/docs` 应显示 Swagger 文档页。
3. 验证健康检查：浏览器打开 `http://127.0.0.1:8000/health` 应返回 `{"status":"ok","db":"data/invest.db"}`。
4. 验证一个数据接口：`http://127.0.0.1:8000/api/strength?period=short&top=3` 应返回行业强度 JSON 数组。

**回报**：第 3 步的 /health 输出。若暂时不需要 API，此项可跳过。

---

## 8. 环境重评触发条件落地（ERP 跨分位 / 社融拐点 / 10Y>20bp）

**目的**：把总闸从「手工评级」升级为「数据触发自动重评」。需要三个数据源先确认，再改代码。

**前置数据源确认（按顺序做，回报结果）**：

1. **10Y 国债收益率**：浏览器打开
   ```
   https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_BOND_CB_HSL&columns=ALL&pageNumber=1&pageSize=3
   ```
   或直接跑：
   ```bat
   myenv\Scripts\python.exe -c "import akshare as ak; df=ak.bond_zh_us_rate(start_date='20260101'); print(df.tail(3))"
   ```
   （预期：有 10 年期国债收益率列，如 `中国国债收益率10年`）
2. **全 A 股平均 PE（算 ERP=1/PE − 10Y）**：
   ```bat
   myenv\Scripts\python.exe -c "import akshare as ak; df=ak.stock_a_ttm_lyr(); print(df.tail(3))"
   ```
   （预期：`乐咕乐股A股平均市盈率` 等列）
3. **社融增量**：同第 2 项的结果（字段名列表）。

**回报**：第 1、2 步的输出（成功/报错原文）+ 第 3 步字段名。数据源确认后，我来写 ERP 分位计算、社融拐点检测、10Y 周变动检测，挂入 `invest/discipline/macro_gate.py` 与调度器，并配单测。
