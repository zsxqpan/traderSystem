# 报告类复用 · 轻量角度 skill 接入报告 · 设计文档

> 日期：2026-08-23 ｜ 分支：dev/2026-08-23
> 方法：grilling（一轮设计树对齐）+ brainstorming（architectural 路径）
> 前置：2026-08-23 已落地 9 个轻量角度 skill（.claude/skills/）与 big_v 数据层（Phase 1+2）
> 范围：**把其中适合报告的能力反哺报告体系（invest/skills/），接入 4 项；大V 日报段列二期**
> 代码实现留待本 spec 审阅通过后的实施步骤

---

## 1. 背景与目标

### 1.1 背景
- 报告体系已 skill 化（2026-08-22）：7 报告 + 27 小节，全部薄包装 `invest/report.py` 纯函数，
  LLM 调用均为**单轮提炼**（无 agent 工具循环），定时任务驱动。
- 轻量角度 skill 已落地（2026-08-23），其中 4 个角度天然适合反哺报告：
  - `opinion-analysis`（舆情）→ 报告目前只有财联社电报单源消息面（D1/D27），无社区舆情；
  - `sector-analysis`（板块）→ 报告现有资金主线（D13）/板块异动（D14）是**分散的单一维度**，缺强度+资金+联动共振判断；
  - `trap-scan`（杀猪盘）→ 候选池/持仓目前无风险扫描（仅持仓警戒 D16 基于价格）；
  - `stock-cycle`（周期）→ 周报缺周期行业定位段。
- `big-v-monitor`（大V）→ big_v 表已建但为空（无采集器），日报段**列二期**。

### 1.2 目标（本阶段）
1. 新增 4 个小节 skill：`d28_community_hot`（社区热议）/ `d29_sector_resonance`（板块共振）/
   `d30_cycle_position`（周期行业定位）/ `d31_pool_trap_alerts`（候选池预警）；
2. 接入报告：a3_daily（+d28+d29）、b1_intraday（+d29）、a4_weekly（+d30）；
3. 新增定时任务 `pool_trap_scan`（盘后 17:10 全 8 信号扫描候选池/持仓）；
4. 新增表 `pool_trap_alerts`（SCHEMA_VERSION 9→10）；
5. **不改变任何现有小节输出**（D1/D11/D13/D14 等逐字节不变）。

### 1.3 非目标（明确不做）
- 不做大V 日报段（big_v 表空，等二期采集器）；
- 不做 industry_cycle 采集器（d30 用规则版，二期填表后增强）；
- 不改造现有小节（不替换 D1/D13/D14 实现）；
- 不引入 agent 工具循环进报告（保持单轮 LLM 提炼）。

---

## 2. 设计原则（用户确认）

| 原则 | 内容 |
|---|---|
| 舆情形态 | 新增独立小节（D1 保持电报提炼不动），搜索≤2 次 + LLM 1 次 |
| 板块形态 | 新增「板块共振」小节（强度+资金+联动三表共振），D13/D14 原样保留，纯规则 0 LLM |
| 杀猪盘策略 | 候选池/持仓逐股全 8 信号，每日盘后扫描 + 预警清单（当前候选池 2 只，成本可忽略） |
| 数据缺口 | 周期用规则版先落地（中线强度/资金/估值推断，0 LLM）；大V 二期 |
| 交付 | 先 spec 评审，通过后实现 |

**报告侧硬约束**（实现必须遵守）：
- 报告代码内 LLM 仅单轮提炼（`LLMClient.run(max_turns=1)`），无工具循环；
- 联网统一走 `invest.agent.web_tools.web_search`（requests 直连，`trust_env=False`），失败静默；
- 每个新小节失败必须回退空串/直列，**不阻断报告**（与现有 `_xxx_block` 模式一致）；
- 新增成本上限：每报告 LLM 新增 ≤1 次、web_search ≤2 次。

---

## 3. 新小节定义（4 个）

### 3.1 d28_community_hot · 社区热议（复用 opinion-analysis 方法论）
- 文件：`invest/skills/sections/d28_community_hot.py`
- 数据流：
  1. `web_search("site:xueqiu.com {日期} 股票 热议", n=5)` + `web_search("site:guba.eastmoney.com 热议", n=5)`（2 次，失败任一次则用另一源）；
  2. LLM 单轮提炼（job='daily_report'）：从搜索摘要挑 2-3 条"社区热议"（主题+一句话观点+平台），
     忽略营销/重复内容，禁止编造；
  3. 失败回退：`（暂无社区热议素材）`。
- 输出模板：
  ```
  【社区热议 · 雪球/股吧】
    - {主题}｜{一句话观点}（{平台}）
  ```
- 接入：a3_daily 尾部（消息面之后）。
- 成本：搜索 2 次 + LLM 1 次（~1-2K token）。

### 3.2 d29_sector_resonance · 板块共振（复用 sector-analysis 方法论）
- 文件：`invest/skills/sections/d29_sector_resonance.py`
- 数据流（纯规则，无 LLM）：
  1. `quant_strength`（obj_type=industry, period=short, 最新 run_date）RS TOP15；
  2. `sector_fund_flow`（最新日期）主力净流入 TOP15；
  3. 取交集（强度与资金同向共振）按 RS 降序 TOP3；
  4. 补 `quant_linkage`（该行业高相关联动伙伴，corr≥0.7 取 top1）。
- 输出模板：
  ```
  【板块共振 TOP3】
    {行业} rs{+x%} 主力净流入{+X亿} 联动:{伙伴}
  ```
- 数据缺失（strength 或 fund_flow 空）→ 空串。
- 接入：a3_daily + b1_intraday 尾部。
- 成本：0。

### 3.3 d30_cycle_position · 周期行业定位（复用 stock-cycle 方法论）
- 文件：`invest/skills/sections/d30_cycle_position.py`
- 周期行业白名单（模块级常量）：有色 / 煤炭 / 钢铁 / 化工 / 航运 / 养殖 / 房地产 / 建材 / 工程机械（可扩展）。
- 数据流（纯规则，无 LLM）：
  1. 白名单 ∩ `quant_strength`（industry, period=mid, 最新）取 RS 与 trend_stage；
  2. `sector_fund_flow` 最新主力净流入（有则显示）；
  3. `quant_valuation`（最新 pe_pct，有则显示）；
  4. 阶段判定：RS>0 且资金净流入 → 上行；RS<0 且资金净流出 → 下行；pe_pct<30% → 筑底（优先）；
     pe_pct>85% → 过热（优先）；其余 → 震荡。
- 输出模板：
  ```
  【周期行业定位】
    {行业}：{上行/下行/筑底/过热/震荡}（中线 rs{+x%} · PE分位{y%}）
  ```
- 无数据行业跳过；全部无数据 → 空串。
- 接入：a4_weekly（消息面之前）。
- 二期增强：industry_cycle 表填充后改读 key_indicators 引用。
- 成本：0。

### 3.4 d31_pool_trap_alerts · 候选池预警（复用 trap-scan 完整 8 信号）
- 文件：`invest/skills/sections/d31_pool_trap_alerts.py`（render 返回文本，供报告/推送复用；核心扫描函数供定时任务调用）
- 扫描范围（去重）：`candidate_pool`（out_date IS NULL，任意 level）+ `cards`（status IN locked/review）。
  当前约 2 只。
- 8 信号执行：
  | 信号 | 来源 | 判定 |
  |---|---|---|
  | ④ 基本面热度脱节 | 本地 `daily_bars` + `dragon_tiger` + `limit_up_pool` | 有热度（上榜/异动）但财务数据缺失或估值异常高 |
  | ⑤ K线异常配合 | 本地 `daily_bars` | 近 5 日累计涨幅 ≥15% 或涨停池成员 |
  | ①②③⑥⑦⑧ 软信号 | `web_search`（每条信号 1 次，关键词合并复用结果） | 命中推广话术/社群引流/跨平台联动等关键词即计入 |
- 评分：命中数 → 🟢0-1 / 🟡2-3 / 🟠4-5 / 🔴6+；软信号搜不到标"数据不足"不硬判。
- 输出：
  1. 写 `pool_trap_alerts` 表（`storage.upsert_df`，主键 (date,symbol,src)）；
  2. 有 ≥🟡 的票 → 推送预警（默认飞书 `send_message`，失败静默）。
- 接入：定时任务（§5），render 供日报/手动查询复用。
- 成本：搜索 ≤2 次/票（软信号 6 条合并 2 组关键词），当前 2 票 → ≤4 次；0 LLM。
- 预留开关：候选池 >10 只时软信号降为每周（模块级常量 `SOFT_SCAN_DAILY=True` 可关）。

---

## 4. 报告编排改动

| 报告 skill | 新增小节 | 位置 | uses 追加 |
|---|---|---|---|
| a3_daily | d28_community_hot + d29_sector_resonance | 尾部（消息面之后） | "d28_community_hot", "d29_sector_resonance" |
| b1_intraday | d29_sector_resonance | 尾部 | "d29_sector_resonance" |
| a4_weekly | d30_cycle_position | 消息面之前 | "d30_cycle_position" |

- 实现方式：报告 render 中调用小节 `render()` 拿文本 append 到 sections（与现有 D1 用法一致）；
- `registry.validate_all()` 校验 uses 引用存在（新增小节注册后自动覆盖）。

---

## 5. 定时任务

- `invest/scheduler.py`：
  - `JOB_FUNCS` 注册 `"pool_trap_scan": _pool_trap_scan`；
  - `_pool_trap_scan(db, conn)`：扫描候选池/持仓（§3.4 逻辑）→ 写 `pool_trap_alerts` → 有 ≥🟡 推飞书；
  - `add_job(CronTrigger(day_of_week="mon-fri", hour=17, minute=10), id="pool_trap_scan")`
    （在 snapshot_close 16:10 之后、evening_report 22:00 之前）；
- **同步更新** `tests/test_pipeline.py::test_scheduler_jobs` 的 JOB_FUNCS 断言。

---

## 6. 表结构（SCHEMA_VERSION 9→10）

```sql
CREATE TABLE IF NOT EXISTS pool_trap_alerts (
    date           TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    level          TEXT NOT NULL,           -- 🟢 / 🟡 / 🟠 / 🔴
    trap_score     REAL,                    -- 反向分，越高越安全
    signals_hit    TEXT,                    -- JSON 数组 [{id,name,evidence,severity}]
    recommendation TEXT,
    src            TEXT NOT NULL DEFAULT 'trap_scan',
    PRIMARY KEY (date, symbol, src)
);
```

- 加进 `invest/db.py` 的 `SCHEMA_SQL`；SCHEMA_VERSION 9→10；对真实库跑 `init_db` 生效。

---

## 7. 成本预算汇总

| 小节 | LLM 调用 | web_search | 进哪些报告 | 失败行为 |
|---|---|---|---|---|
| d28 社区热议 | 1 | 2 | 日报 | 空串 |
| d29 板块共振 | 0 | 0 | 日报+盘中 | 空串 |
| d30 周期行业定位 | 0 | 0 | 周报 | 空串 |
| d31 候选池预警 | 0 | ≤4（2票×2） | 定时推送 | 表不写/不推 |

---

## 8. 测试清单（全 mock 不联网）

1. `tests/test_skills.py`：
   - 注册表 34→38（4 个新小节），`validate_all()` 无问题；
   - d28 render 确定性：mock `web_tools.web_search` + `LLMClient`；
   - d29 确定性：种子 quant_strength/sector_fund_flow/quant_linkage 数据断言交集与排序；
   - d30 确定性：种子周期行业数据断言阶段判定（上行/筑底等）；
   - d31 确定性：种子 candidate_pool/cards + mock web_search，断言评分与输出。
2. `tests/test_pipeline.py`：`pool_trap_scan` 注册 + 调度时间断言。
3. `tests/test_report*.py`（相关）：日报/周报含新小节文本（mock 联网/LLM）。
4. `ruff check invest tests` 0 错误。

---

## 9. 二期规划（不属本 spec）

- **大V 日报段**：等社区采集器（cookie 登录雪球）填充 big_v_* 表后，新增小节展示"大V观点"；
- **industry_cycle 采集器**：填充后 d30 引用 key_indicators 增强；
- **舆情数据源升级**：社区采集器入库后，d28 改读表（去掉 web_search 依赖，更稳更省）；
- **候选池扩容**（>10 只）：软信号降频开关生效。

---

## 10. 自审清单

- [x] 无占位符：4 小节数据流/判定/接入点均已明确；
- [x] 内部一致：§2 原则 ↔ §3 小节定义 ↔ §4/§5 接入 ↔ §6 表 ↔ §7 成本一一对应；
- [x] 范围聚焦：不替换现有小节、不动对话 agent、不做二期项；
- [x] 歧义消解：杀猪盘"全 8 信号"明确硬/软信号来源与评分；周期"规则版"明确四阶段判定；
- [x] 与既有资产边界：报告 skill 化项目（2026-08-22）、角度 skill 项目（2026-08-23）均向后兼容。

---

## 11. 待用户确认项（评审时逐项确认）

1. 小节编号 d28-d31 顺延分配；
2. 候选池预警推送渠道：默认飞书（≥🟡 才推），企微是否需要；
3. 日报内新小节位置：尾部（消息面之后）；
4. 实施顺序：spec 批准后按 3.1→3.4 → 报告编排 → 定时任务 → 测试。
