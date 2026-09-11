# 辅助交易完善 · 实现任务清单

> **状态：已落地（2026-09-09）**
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Follow @test-driven-development. Do not skip tests. Do not touch `a5_monthly` / `a6_yearly`.

**Goal:** 把信号引擎扩成短线+中线、关注层+发现层的总线，接到盘前/竞价/盘中/盘后/周报和仪表盘，增强辅助交易，不改成量化下单 agent。

**Architecture:** 一次 `scan` / 一次落库，消费方按 `horizon × session × layer × severity` 取子集。中线规则只读已有 `quant_*` 表（收盘 `pipeline.quant` 之后 persist `session=daily`）。发现层用涨停核心 + 高开榜 + RS 行业核心股 + 龙虎代码扩宇宙，禁止全市场扫 K 线。报告骨架不大改。

**Tech Stack:** Python 3.10+、SQLite `invest/db.py`、pytest（全 mock 不联网）、ruff、Streamlit `dashboard/`。

**规格来源:** `docs/plans/2026-09-09-辅助交易完善-design.md`（已拍板）。今天全部改动与现网待办：`docs/superpowers/specs/2026-09-09-辅助交易改动说明.md`。

**约定（每项任务都遵守）**

- 虚拟环境 `myenv/`；测试：`myenv/bin/python -m pytest <file> -v`（本机若是 Windows 则 `myenv\Scripts\python.exe`）。
- 新测试一律 tempfile + `init_db`，**禁止连真实网络、禁止依赖 `data/invest.db` 里的生产数据**（现有 `tests/test_dashboard.py` 读真实库，**不要往里加新断言**；仪表盘新查询另写 `tests/test_dashboard_signals.py`）。
- 网络请求保持 `trust_env=False` / `ProxyHandler({})`；本阶段规则层应不发请求。
- 文案：对象+数字+含义一句；禁止「建议买入」。
- 每完成一个 Task：相关 pytest 绿 + `ruff check` 触及文件 0 error。提交由用户点头后再 commit（本清单里的 commit 文案是建议，不要擅自 commit）。
- **禁止修改** `invest/skills/reports/a5_monthly.py`、`a6_yearly.py` 及对应测试里专测月报/年报结构的断言（除非全量注册表计数被迫更新——本期不新增报告 skill，计数不应变）。

---

## 依赖与顺序

```
A 模型/查询 ──► B 中线规则 ──► D 仪表盘（散点+信号页可先做）
       │
       └──► C 短线发现宇宙 ──► E 报告完善 ──► F 冻结核对与文档
```

- **A 必须先做完**，否则后面没有 `horizon/layer`。
- B 与 C 在 A 之后可串行（建议先 B，因仪表盘主视觉是坐标系）。
- D 依赖 A+B（中线有数据形状）；C 未完成前仪表盘短线页可以只有一期信号。
- E 依赖 B+C。
- F 最后。

现网：改完 A 后必须对真实库跑一次 `init_db`（SCHEMA 12→13），否则新列不存在。

---

## 任务总表

| ID | 阶段 | 内容 | 主要文件 | 状态 |
|---|---|---|---|---|
| A1 | A | Schema 12→13，`horizon`/`layer` 列 + 迁移 | `invest/db.py` | ✅ |
| A2 | A | `Signal` 字段、layer 推断、阈值常量扩展 | `types.py` `thresholds.py` | ✅ |
| A3 | A | persist 写新列；缺列兼容 | `persist.py` | ✅ |
| A4 | A | `scan` 支持 `daily`、过滤 horizon/layers；不传参=旧行为 | `scan.py` | ✅ |
| A5 | A | `list_signals` 只读查询 | 新建 `query.py` | ✅ |
| A6 | A | `pick`/`format`/`b1` 拆主节与发现 action | `format.py` | ✅ |
| A7 | A | d32 参数 + `run_section` schema | `d32_*.py` `tools.py` | ✅ |
| A8 | A | 一期回归（旧测试全绿） | `tests/test_signals.py` | ✅ |
| B1 | B | 四象限规则 `quad_*` | 新建 `mid.py` | ✅ |
| B2 | B | `quad_path` 五日轨迹 | `mid.py` | ✅ |
| B3 | B | 趋势阶段当日变化 | `mid.py` | ✅ |
| B4 | B | 轮动领涨/滞后 | `mid.py` | ✅ |
| B5 | B | 共振 + `mid_rs_top` | `mid.py` | ✅ |
| B6 | B | `style_shift` + `emotion_stage_shift` | `mid.py` | ✅ |
| B7 | B | `scan(session=daily)` + `quant()` 末尾 persist | `scan.py` `pipeline.py` | ✅ |
| B8 | B | 中线分组文本（盘前/盘后/周报/仪表盘共用） | `format.py` | ✅ |
| C1 | C | 发现宇宙 + 上限断言 | `universe.py` | ✅ |
| C2 | C | RS TOP 行业核心股 | `universe.py` | ✅ |
| C3 | C | shrink/highvol 扫 discovery（含 RS 核心） | `rules.py` `scan.py` | ✅ |
| C4 | C | 龙虎代码入宇宙（只扩股票列表） | `universe.py` | ✅ |
| C5 | C | 相对昨竞价保量 | `rules.py` | ✅ |
| C6 | C | 高开/低开放量信号 | `rules.py` | ✅ |
| C7 | C | 板块资金放大 | `rules.py` | ✅ |
| C8 | C | 连板晋级/断板 | `rules.py` | ✅ |
| C9 | C | `rs_industry_leader` | `mid.py` 或 `rules.py` | ✅ |
| C10 | C | auction/intraday 批量行情覆盖发现层 | `scan.py` `b1_intraday.py` | ✅ |
| D1 | D | 散点查询补 `crowding_state`、象限 | `dashboard/queries.py` | ✅ |
| D2 | D | `load_signals` 筛选查询 | `queries.py` | ✅ |
| D3 | D | 新页「交易信号」 | `dashboard/app.py` | ✅ |
| D4 | D | 总览散点视觉 | `app.py` | ✅ |
| D5 | D | 短线轨/中线轨附表（四象限都展示） | `app.py` | ✅ |
| D6 | D | 轮动页轻量标注（可选小改） | `app.py` | ✅ |
| E1 | E | 报告用 format 辅助（未消化不限池、机会节） | `format.py` | ✅ |
| E2 | E | a7 竞价接入 | `a7_auction.py` | ✅ |
| E3 | E | b1 主节 +「明确发现」 | `b1_intraday.py` | ✅ |
| E4 | E | a0 未消化 + 市场机会 | `a0_premarket.py` | ✅ |
| E5 | E | a3 点1 四象限 + 注入加长 | `a3_daily.py` | ✅ |
| E6 | E | a4 周报中线信号节 | `a4_weekly.py` 和/或 `report.py` | ✅ |
| E7 | E | LLM prompt：禁止编造象限；picks 优先 hunt | `_daily_llm.py` `_intraday_llm.py` | ✅ |
| E8 | E | 落库点：quant daily；evening close；auction 带榜 | `pipeline.py` `scheduler.py` | ✅ |
| F1 | F | 冻结核对（不新增入口） | 人工+测试 | ✅ |
| F2 | F | 文档状态 | design / AGENTS 可选一句 | ✅ |
| F3 | F | 全相关 pytest + ruff | — | ✅ |

---

## 阶段 A — 模型与查询

一期兼容铁律：**不传 `horizon`/`layers` 时，`scan(..., session in auction|intraday|close)` 行为与现在一致**（短线、同一套规则、`pick` 默认仍 8 条）。新字段用默认值：`horizon='short'`，layer 由宇宙推断。

### Task A1: Schema 12 → 13

**Files:**
- Modify: `invest/db.py`（`SCHEMA_VERSION`、`SCHEMA_SQL` 里 `trade_signals`、`_migrate`）
- Test: `tests/test_signals.py`（扩展 `test_schema_has_signal_tables`）

**Step 1: 写失败测试**

```python
def test_schema_has_horizon_layer_columns():
    p = _tmp_db()
    conn = connect(p)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_signals)")}
        assert "horizon" in cols
        assert "layer" in cols
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver >= 13
    finally:
        conn.close()
```

另写：先建旧表再 `_migrate` 能加列（可用手工 `CREATE TABLE` 无新列的临时库，或测 `ALTER` 幂等：跑两次 `init_db` 不报错）。

**Step 2:** `pytest tests/test_signals.py::test_schema_has_horizon_layer_columns -v` → FAIL（无列 / version=12）

**Step 3: 实现**

1. `SCHEMA_VERSION = 13`
2. `CREATE TABLE trade_signals` 增加：
   - `horizon TEXT NOT NULL DEFAULT 'short'`  -- short / mid
   - `layer TEXT NOT NULL DEFAULT 'watch'`    -- watch / discovery / market
3. `_migrate`：

```python
cols_sig = [r["name"] for r in conn.execute("PRAGMA table_info(trade_signals)")]
if cols_sig:  # 表已存在
    if "horizon" not in cols_sig:
        conn.execute("ALTER TABLE trade_signals ADD COLUMN horizon TEXT NOT NULL DEFAULT 'short'")
    if "layer" not in cols_sig:
        conn.execute("ALTER TABLE trade_signals ADD COLUMN layer TEXT NOT NULL DEFAULT 'watch'")
```

主键仍 `(date, session, signal_id, subject)`。注释里 session 补 `daily`。

**Step 4:** 测试 PASS。现网之后跑一次 `init_db`。

---

### Task A2: Signal 数据类 + 阈值 + layer 推断

**Files:**
- Modify: `invest/signals/types.py`
- Modify: `invest/signals/thresholds.py`
- Create 或 Modify: `invest/signals/universe.py`（加 `assign_layer`）
- Test: `tests/test_signals.py`

**Signal 默认字段（旧测试不改构造参数也能跑）：**

```python
SESSIONS = ("auction", "intraday", "close", "daily")
HORIZONS = ("short", "mid")
LAYERS = ("watch", "discovery", "market")

@dataclass
class Signal:
    id: str
    name: str
    session: str
    severity: str
    subject_type: str
    subject: str
    hint: str
    evidence: dict = field(default_factory=dict)
    horizon: str = "short"
    layer: str = "watch"
```

**`assign_layer(subject_type, subject, watch: set[str], discovery: set[str]) -> str`**

- `subject_type in ("market", "etf")` → `market`（规则也可在构造时显式传入）
- `subject_type == "sector"` → `discovery`（象限回避可在规则里写死 `layer='market'`）
- `subject` 在 watch → `watch`
- 否则 → `discovery`

**thresholds.py 新增（全部集中，禁止散落魔法数）：**

```python
# 展示
DISPLAY_LIMIT = 8              # 一期默认，兼容
DISPLAY_B1_WATCH = 12
DISPLAY_B1_DISCOVERY_ACTION = 5
DISPLAY_A7 = 12
DISPLAY_A3 = 12
DISPLAY_WIDE = 30              # a0 / a4 / 仪表盘

DISCOVERY_STOCK_CAP = 80
RS_INDUSTRY_TOP = 8
RS_CORE_PER_INDUSTRY = 3
BOARD_TOP_N = 10

AUCTION_YOY = 0.80             # 今竞价量/昨竞价量
BOARD_HIGH_OPEN_PCT = 3.0      # 百分点
BOARD_LOW_OPEN_PCT = -3.0
FLOW_SPIKE = 2.0
RS_LEADER_RANK = 8
RS_LEADER_UP = 3               # 排名上升至少 N 名（rank 变小）
ROTATION_LEAD_RANK = 15
QUAD_RS_ZERO = 0.0
QUAD_CROWDING = 0.8            # 与仪表盘 hline 一致
QUAD_HUNT_LIMIT = 8
QUAD_CHASE_LIMIT = 8
QUAD_WATCH_LIMIT = 6
PATH_DAYS = 5
```

**测试:** `assign_layer` 三例；`Signal()` 不传 horizon 时为 short。

---

### Task A3: persist 写入 horizon/layer

**Files:** Modify `invest/signals/persist.py`  
**Test:** 扩展 `test_persist_signals_and_auction_snapshots`

INSERT 增加两列。读取断言：

```python
row = conn.execute(
    "SELECT horizon, layer FROM trade_signals WHERE subject='600519'"
).fetchone()
assert row["horizon"] == "short"
assert row["layer"] in ("watch", "discovery")
```

同日同 session 仍先 DELETE 再 INSERT。`session=daily` 与 `close` 互不删除。

---

### Task A4: scan 扩展且默认兼容

**Files:** Modify `invest/signals/scan.py`

签名改为：

```python
def scan(
    conn, session: str,
    asof=None, now=None,
    quotes=None, etf_quotes=None,
    persist: bool = False,
    limit: int = DISPLAY_LIMIT,
    horizon: str | None = None,      # None=不按 horizon 滤（一期=全是 short）
    layers: list[str] | None = None, # None=不过滤
    boards: list[dict] | None = None,  # 竞价高低开放量榜，C6 再用，A 先收下不跑新规则
) -> list[Signal]:
```

- `session` 允许 `daily`：A4 可先只跑空列表（B7 接中线），但**不得**对 daily 调 `_fetch_quotes`。
- `session in (auction, intraday)` 且 `quotes is None` 才联网；测试必须继续注入 quotes。
- 每条 Signal 在 append 前填 `horizon`/`layer`（短线规则先全部 `horizon=short`，layer 用 `assign_layer`；市场结构规则显式 `layer='market'`）。
- 返回前：若 `horizon`/`layers` 有值则过滤，再 `pick_signals(..., limit)`。
- **默认 `scan(conn, "auction", quotes=...)` 与一期相同命中集合**（layer 字段多了但不改变谁被 pick）。

**测试:**
- 旧 `test_auction_keep_vol_hits_limit_up_stock` 仍过。
- 新 `test_scan_daily_does_not_fetch_quotes`：monkeypatch `_fetch_quotes` 为 raise，`scan(conn,"daily")` 不抛。
- 新 `test_scan_layers_filter`：造 watch+discovery 两条，`layers=["watch"]` 只剩 watch。

---

### Task A5: `list_signals` 只读

**Files:** Create `invest/signals/query.py`  
**Export:** `invest/signals/__init__.py`

```python
def list_signals(
    conn,
    asof: date | None = None,
    *,
    horizon: str | None = None,
    session: str | None = None,
    layer: str | None = None,
    severity: str | None = None,
    limit: int = 200,
) -> list[dict]:
```

SQL 动态 AND 条件，参数化。`asof is None` → `date = (SELECT MAX(date) FROM trade_signals)`。  
缺 `horizon` 列时（未 migrate）catch 后当全部 short/watch，或直接要求 init_db——**实现选：缺列返回 [] 并 log warning**，避免仪表盘炸。

**测试:** persist 两条不同 layer，按 layer 过滤条数。

---

### Task A6: 格式化与 b1 拆节

**Files:** Modify `invest/signals/format.py`

新增：

```python
def pick_signals(signals, limit=DISPLAY_LIMIT, *, layers=None, horizon=None, severities=None):
    """在现有 (id,subject) 去重 + action 优先 之上加过滤。"""

def split_b1(signals) -> tuple[list[Signal], list[Signal]]:
    """主节 = watch∪market 的 short；发现节 = discovery 且 action，最多 DISPLAY_B1_DISCOVERY_ACTION。"""

def format_signals(signals, limit=..., title="【交易信号】") -> str:

def format_discovery_action(signals) -> str:
    """无命中返回空串。标题【明确发现】。"""

def signal_section(...)  # 保持；可加 title 参数
```

**测试:**
- 10 条 discovery watch + 3 条 discovery action + 若干 watch → `split_b1` 发现节 ≤5 且全是 action，主节不含 discovery watch。
- `pick_signals` limit=8 旧测试仍过。

`undigested_actions` **本任务先不动**（E1 再取消必须在池内）。

---

### Task A7: d32 + 对话工具参数

**Files:**
- Modify: `invest/skills/sections/d32_trade_signals.py`
- Modify: `invest/agent/tools.py`（`run_section` parameters：`horizon`、`layer`；description 补 daily/short/mid）
- Test: `tests/test_agent.py`（描述含 d32/horizon）；`tests/test_signals.py::test_d32_render_and_pick_limit`

`render(db_path, session="intraday", horizon="", layer="")`：空字符串当 None。`session=daily` 走 `list_signals` 或 `scan(..., persist=False)`。  
**render 仍无副作用不落库。**

小节数量仍为 32，不要新 d33。`tests/test_skills.py` `len(sections)==32` 不变。

---

### Task A8: 一期回归

Run:

```
myenv/bin/python -m pytest tests/test_signals.py tests/test_skills.py tests/test_pipeline.py::test_notify_messages_no_crash tests/test_agent.py -q
ruff check invest/signals invest/db.py invest/skills/sections/d32_trade_signals.py invest/agent/tools.py
```

Expected: 全绿，ruff 0。旧 overlay 测试仍过（`auto.py` 读 `trade_signals` 可不选新列）。

---

## 阶段 B — 中线规则

全部 **只读 SQLite，不联网**。新建 `invest/signals/mid.py`，由 `scan(..., session="daily")` 调用。`horizon='mid'`。

假数据手法：往 `quant_strength` / `quant_valuation` / `quant_rotation` / `sector_fund_flow` / `market_emotion` / `quant_strength obj_type=index` 插入两日截面。

### Task B1: 四象限 `quad_hunt/chase/watch_cheap/avoid`

**Files:** Create `invest/signals/mid.py`；Modify `thresholds.py`（已在 A2）  
**Test:** `tests/test_signals.py` 或新建 `tests/test_signals_mid.py`（推荐拆文件，避免单文件过大）

对齐仪表盘：短线 `quant_strength period='short' obj_type='industry'` 最新 `run_date` ⋈ 最新 `quant_valuation`。

| id | 条件 | layer | 上限 |
|---|---|---|---|
| `quad_hunt` | rs>0 且 crowding<0.8（优先 crowding_state 正常/升温） | discovery | 8，按 rs 降序 |
| `quad_chase` | rs>0 且 crowding≥0.8（或 state 含高拥挤/极端） | discovery | 8 |
| `quad_watch_cheap` | rs≤0 且 crowding<0.8 且 trend_stage==启动 | discovery | 6 |
| `quad_avoid` | rs≤0 且 crowding≥0.8，**或** crowding_state==极端且恶化 | market | 全列（行业数有限） |

hint 必须含 rs、crowding 分位、state。同一行业只进一个象限（互斥优先级：avoid > chase > hunt > watch_cheap——**极端且恶化即使 rs>0 也进 avoid**，与「都要」不冲突：四类都会在市场中出现，单标的不重复）。

**测试:** 造四个行业各落一象限，断言四个 id 都出现；极端且恶化+rs>0 → avoid 不是 chase。

---

### Task B2: `quad_path`

近 `PATH_DAYS` 个 `run_date`：若某行业从 chase 条件变为 hunt 条件（拥挤回落、rs 仍正）→ 一条 path，hint「拥挤回落仍强」；反向 hunt→chase → hint「进入拥挤」。无变化不出。

**测试:** 5 日 crowding 0.90→0.70、rs 一直为正 → 1 条 path。

---

### Task B3: `stage_start/accel/slow/break`

映射：启动/加速/减速/破位。仅 **当日 trend_stage ≠ 昨日** 才出。`subject_type=sector`。

**测试:** 昨震荡今启动 → `stage_start`；连续两日启动 → 无。

---

### Task B4: `rotation_lead` / `rotation_lag`

读 `quant_rotation` 最新日：`lead_lag==领涨` 且 `rank<=15` → lead；`lead_lag==滞后` → lag。

**测试:** 两条行业各中一个。

---

### Task B5: `sector_resonance` + `mid_rs_top`

- resonance：复用 D29 逻辑（RS short TOP15 ∩ `sector_fund_flow` 当日主力净流入 TOP15），最多 5，不要 import d29 的 render 文本，抽 **共享纯函数**（可把交集计算放到 `mid.py`，d29 以后可改调——**本期允许 d29 暂不重构**，mid 复制 SQL 即可，避免大改报告小节）。
- `mid_rs_top`：`period='mid'` RS 前 8 且 trend_stage ≠ 破位，`severity=info`。

**测试:** 交集 1 个行业 → 1 条 resonance；破位行业不进 mid_rs_top。

---

### Task B6: `style_shift` + `emotion_stage_shift`

**style_shift:** 无独立风格表。用 `quant_strength obj_type='index' period='short'` 近两个 `run_date`，按 `invest/quant/style.py` 同口径比较大小盘（000852+000905 vs 000016+000300）和成长（000688+399006）RS 差的符号是否翻转。无翻转不出。`layer=market`，`subject=风格`。

**emotion_stage_shift:** 调 `invest.quant.emotion_cycle.emotion_cycle` 对最近两日 `market_emotion`（含昨日均值所需历史）得 stage，若 stage 变化则出，evidence 带 from/to。`layer=market`。可放 mid.py（session=daily），close 短线不再重复。

**测试:** 两日 index RS 翻转 → 1 条；情绪冰点→启动 → 1 条；不变 → 0。

---

### Task B7: daily scan + quant 末尾 persist

**Files:** `invest/signals/scan.py`、`invest/pipeline.py`（`quant()` 在 upsert 成功、`conn` 仍打开或重新 connect 之后）

```python
# pipeline.quant 末尾，失败只 log，不抛，不影响 counts 返回
from invest.signals.scan import scan as scan_signals
scan_signals(conn, "daily", persist=True, limit=10_000)  # 落库全量，pick 上限拉大
```

`scan daily`：**禁止** `_fetch_quotes`。只跑 `mid.py` 全部规则。

**测试:**
- `test_pipeline_quant` 仍过；可补：quant 后 `trade_signals` 有 `session='daily'`（种子数据足够时）。
- 假表空 → daily 返回 `[]` 不抛。

**落库时机说明（写进代码注释）：** 调度 `_after_close` 16:00 已 `quant`，22:00 `evening_report` 读得到中线；不要把 daily persist 只放在 `notify_after_close`（生产 22:00 走的是 `evening_report`→`a3_daily`，**不是** `notify_after_close`）。

---

### Task B8: 中线分组文本

**Files:** `invest/signals/format.py`

```python
def format_mid_quadrants(signals: list[Signal]) -> str:
    """按 hunt/chase/watch_cheap/avoid 分组；某组空则跳过；全空返回空串。
    标题【中线战场】。"""
```

**测试:** 四组都有至少 1 条时四组标题都在；只有 hunt 时没有「回避」空壳。

---

## 阶段 C — 短线变厚 + 发现宇宙

### Task C1: 发现宇宙骨架 + 上限

**Files:** `invest/signals/universe.py`

```python
def discovery_symbols(conn, asof, *, boards=None, cap=DISCOVERY_STOCK_CAP) -> list[str]:
    """昨涨停 ∪ 热门板块核心 ∪ boards 代码 ∪（C2）RS 核心 ∪（C4）龙虎。
    去重保序，截断 cap。不含已在 watch 的代码也可以含——layer 推断会标 watch。
    """

def discovery_cap_ok(symbols) -> None:
    assert len(symbols) <= DISCOVERY_STOCK_CAP
```

**测试:** 造 200 个假代码入龙虎/榜，截断后 ≤80。  
**测试:** 出池标的不因 watch 进入；可因涨停进入 discovery。

禁止：`SELECT symbol FROM daily_bars` 无行业过滤。

---

### Task C2: 短线 RS TOP 行业核心股（已拍板：必须包含）

对最新 `quant_strength` short industry 取 `RS_INDUSTRY_TOP`：

1. `stocks_of(industry)`（`invest/data/industry_map.py`）∪ `candidate_pool.industry=该行业` 的代码。
2. 用 `daily_bars` **最近一日** `amount`（或 volume）排序，取 `RS_CORE_PER_INDUSTRY`。
3. 映射为空的行业 **跳过**，不要退回全市场。

**测试:** 半导体 RS 第一，映射里 5 只股票有 amount，只取前 3；银行无映射 → 不出现银行股。

---

### Task C3: shrink / highvol 扫描 watch ∪ discovery

**Files:** `rules.py` `scan.py`

`shrink_highvol_signals` 的股票列表改为 `watch + discovery`（去重）。每条 Signal `layer=assign_layer(...)`。严重级规则保持：缩量破昨收 / 高位放量滞涨 → action，否则 watch。

**测试:** 股票不在池、在 RS 核心宇宙、量比 0.3 且跌破昨收 → 命中且 `layer=='discovery'` 且 `severity=='action'`。  
**测试:** 全市场 5000 只不会被扫描：discovery 函数不被 `daily_bars` 去重计数撑爆（C1 cap）。

---

### Task C4: 龙虎代码入宇宙

最新 `dragon_tiger.date` 的 `DISTINCT symbol`，上限例如 30，并入 discovery。**不上席位解读、不调 LLM。**

**测试:** 龙虎有 000002、池里没有 → discovery 含 000002；无表/空 → 不抛。

---

### Task C5: `auction_keep_vol_yoy`

读 `auction_snapshots` 昨日同标的 `vol`，今 `quotes.vol / 昨vol >= AUCTION_YOY`。宇宙：watch ∪ 昨涨停（与保量一期相同）。无昨快照则跳过该票（不出信号）。

**测试:** 昨快照 vol=100、今 90 → 命中；无快照 → 不因 yoy 命中（仍可走原来的全天量保量）。

---

### Task C6: `board_high_open_vol` / `board_low_open_vol`

`scan(..., session='auction', boards=...)`，`boards` 为 a7 已有三类榜的合并（symbol/name/pct/vol/amount）。规则：

- high：`pct>=BOARD_HIGH_OPEN_PCT` 且（amount≥KEEP_AMOUNT 或 vol 在放量榜）；`severity=watch`，layer=discovery
- low：`pct<=BOARD_LOW_OPEN_PCT` 且放量；`severity=info` 或 watch

**测试:** 注入 boards 不联网即命中。`boards=None` 时 auction **不要**为了信号去调东财（保持测试不联网）；生产在 E2/E8 把 a7 已拉的榜传入。

可把 `fetch_top_gainers` 的 fields 补 `f5/f6` 以便有量额——**仅拍卖数据模块小改**，测试 mock 返回值，不测真实东财。

---

### Task C7: `sector_flow_spike`

`sector_fund_flow`：当日 `main_net>0` 且 ≥ 近 5 日均值 × `FLOW_SPIKE`。`subject_type=sector`，session 挂在 **close 与 daily 都可**；建议 **只在 daily** 出一条，避免 close/daily 重复（PK 不同 session 会存两份）。选定：**只 daily**，a3 点1 读 daily+close。

**测试:** 均值 1 亿、当日 3 亿 → 命中；当日负净流入 → 不中。

---

### Task C8: `lianban_promote` / `lianban_fail`

用 `limit_up_pool` 昨/今（与 `space_signals` 同源）。个股：昨 n-1 板且未炸、今 n 板未炸 → promote（建议 **action**，好进 b1 明确发现）；昨 n 板今未涨停或炸板 → fail（watch）。

**测试:** 昨 2 板今 3 板 → promote action discovery；今炸板 → fail 而非 promote。

---

### Task C9: `rs_industry_leader`

短线行业 RS 最新 rank（按 rs 排序）≤8，且较上一 `run_date` 名次上升 ≥ `RS_LEADER_UP`。`horizon=short`，但算在 **daily** session（与中线一起落库，免盘中联网）。`subject_type=sector`。

**测试:** 昨 rank 12 今 rank 5 → 命中；名次不变 → 不中。

---

### Task C10: 盘中/竞价行情覆盖发现层

**问题:** 现在 `b1` 只 `fetch_batch_quotes(watch)`，discovery 没有报价则 C3 盘中永不着火。

**改:** `scan` 在 `quotes is None` 且 session 为 auction/intraday 时，对 `watch ∪ discovery_symbols` 批量取行情（已有 `fetch_batch_quotes`）。b1 若自己取行情，应取并集后注入，避免扫两次。

**测试:** monkeypatch `fetch_batch_quotes` 记录 symbols，断言含 discovery 代码；长度 ≤ cap+watch。

**单测默认:** 继续注入 quotes，不走联网。

---

## 阶段 D — 仪表盘（盘后复盘，不自动刷新）

新测试文件 `tests/test_dashboard_signals.py`：tempfile 库，不读生产库。

### Task D1: 散点查询增强

**Files:** `dashboard/queries.py` `load_crowding_vs_strength`

SELECT 增加 `v.crowding_state`。可选：子查询该 `obj` 是否出现在当日 `trade_signals session=daily` 且 `signal_id LIKE 'quad_%'`，列 `quad_id`。

**测试:** 插入 strength+valuation+一条 quad_hunt，DataFrame 含 crowding_state 与 quad_id。

---

### Task D2: `load_signals`

封装 `invest.signals.query.list_signals` → DataFrame，列：date, session, horizon, layer, severity, signal_id, name, subject, hint, evidence。

参数与筛选控件一致。evidence 保持字符串即可。

---

### Task D3: 新页「交易信号」

**Files:** `dashboard/app.py` `PAGES` 插入在「市场总览」之后或「短线轨」之前，名 **交易信号**。

- 筛选：`st.selectbox` horizon（全部/short/mid）、session、layer、severity；默认最近一日（查询 MAX(date)）。
- `st.dataframe` 展示。
- 无数据 `st.info`。
- **不要** `st.rerun` 定时器、不要盘中刷新。

---

### Task D4: 总览散点

- hover 增加 crowding_state、quad_id。
- `color` 仍可用 trend_stage；`symbol` 映射：有 quad → diamond，无 → circle（plotly `symbol=`）。
- 保留 vline rs=0、hline crowding=0.8。
- 散点下方小表：近 5 日 `quad_path`（`load_signals(horizon='mid', ...)` 滤 signal_id=quad_path），无则省略。不做动画箭头也可验收。

---

### Task D5: 短线轨 / 中线轨附表

- 短线轨页底：当日 `horizon=short` 表。
- 中线轨页：拥挤度表旁 **四组** `format_mid_quadrants` 或四个 `st.subheader`（主战场/追高风险/观察/回避），都要有占位逻辑（空则写「无」或省略，四类入口都在代码里，不要只做 chase/hunt）。

---

### Task D6: 轮动页（保持小）

轮动轨迹 **不重做**。可选：最新日 `rotation_lead` 行业名 caption 一行。联动网络 **零修改**（冻结）。

---

## 阶段 E — 报告完善（骨架不动）

### Task E1: 未消化 + 机会节文本

**Files:** `format.py`

- `undigested_actions`：昨日 severity=action **不再要求 subject∈watch**；每条标注「池内」或「池外」。
- `format_market_opportunity(mid_signals) -> str`：四象限 + resonance + emotion_stage_shift；空则空串。标题【市场机会（规则）】。

**测试:** 昨日 action 且股票不在池 → 仍出现且含「池外」。

---

### Task E2: a7 竞价

**Files:** `invest/skills/reports/a7_auction.py`

- `scan_db(..., session='auction', quotes=all_quotes, boards=gainers+losers+vol_top, limit=DISPLAY_A7)`。
- 信号节仍在指数之后、高开榜之前；加一句 caption：「信号=过精确阈值；下列榜单仍是发现器」。
- 核心关注表 `tags_for` 只标 watch 层个股。
- 结构顺序不改。

**测试:** `tests/test_skills.py` 里 a7 仍含「交易信号」；mock `fetch_industries` / 榜单。

---

### Task E3: b1 盘中

**Files:** `invest/skills/reports/b1_intraday.py`

- 行情：`watch ∪ discovery_symbols`（C10）。
- `split_b1(sigs)`：主节 `signal_section`；其后「明确发现」`format_discovery_action`，上限 5，无则省略。
- 核心表信号列仍只池内。
- 简洁版同样保留两节。
- 中线象限 **不进 b1**。
- LLM 2 次保留；`signals_text` 用主节+明确发现拼接。

**测试:** 构造 discovery action + discovery watch，render 结果含明确发现对象、不含 discovery watch 的 hint。可用 mock `scan_db` 返回假 Signal 列表（比造全行情简单）。

---

### Task E4: a0 盘前

**Files:** `a0_premarket.py`

- 昨日信号用新 `undigested_actions`。
- 在今日关注附近插入市场机会节：`list_signals(horizon='mid', session='daily')` 再 `format_market_opportunity`。不新增 LLM。
- 10 节骨架其余不动。

**测试:** 库中有 quad_hunt → 盘前 sections 文本含「主战场」或 hunt 行业名。

---

### Task E5: a3 盘后

**Files:** `a3_daily.py`

- 点1 末尾：close 短线清单（limit=DISPLAY_A3，含 discovery）+ `format_mid_quadrants(list_signals daily)`。
- `sig_text` 拼短线+中线，注入点2/3/4。
- 4 点结构、4 次 LLM、ETF 条件解读、尾部警戒/消息/池变化 **不动**。
- **不要改 a5/a6。**

**测试:** mock scan 与 list_signals；断言点1 含四象限标题之一；`plan_data` 仍返回。

---

### Task E6: a4 周报

**Files:** 优先改 `invest/report.py::weekly_report`（a4 只是薄包装），在中线强度段落后追加「中线信号」：最近 5 个 `run_date/date` 的 daily mid，按 `(signal_id, subject)` 去重留最新。

**测试:** `tests/test_skills.py` 或 report 测试：种子 mid 信号后 `weekly_report` 含「中线信号」。确认没有 import/改 a5/a6。

---

### Task E7: LLM prompt

**Files:** `invest/skills/sections/_daily_llm.py`、`_intraday_llm.py`

在已有「禁止编造保量/缩量」旁增加：**禁止编造拥挤度/RS 象限/主战场划分，只能引用规则信号原文**。

`plan_gen_llm` 增加一句：picks 若推荐个股，**优先**落在给定 `quad_hunt` 行业或 discovery 短线命中行业；无信号时仍可探索，**不要因为没有 hunt 就输出空 picks**（不当硬闸）。

不增加 LLM 调用次数、不提高 max_tokens 除非现有截断明显（默认不改 max_tokens）。

**测试:** 现有 skills 测试 mock LLM 仍过即可；可对 prompt 字符串做 `assert "禁止编造" in` 且含「象限」。

---

### Task E8: 生产落库点对齐调度

| 会话 | 谁 persist | 何时 |
|---|---|---|
| daily 中线 | `pipeline.quant` 末尾 | 16:00 `_after_close`、21:30 `industry_refresh` 等每次 quant |
| close 短线 | `a3_daily.render` 或 `_evening_report` 在 `run_structured` **之后** `scan_db(..., 'close', persist=True)` | 22:00。不要只写在没人调的 `notify_after_close` 里——**把 persist 抽到 a3 发送路径**；`notify_after_close` 可继续 persist 以免脚本入口丢 |
| auction | `notify_auction`：把 a7 用过的 quotes/boards 传入，避免第二次空扫丢榜信号 | 9:26 |
| intraday | 可选：b1 每次 @ 覆盖写 session=intraday（同日 DELETE+INSERT）。注意限频 120s，可 persist | 盘中按需 |

**测试:** `test_notify_messages_no_crash` 仍过；补 evening 路径 mock 不要求真推送。

`tests/test_pipeline.py::test_scheduler_jobs` 的 `JOB_FUNCS` 集合 **不准新增 job 名**（无新定时任务）。

---

### Task E9: 报告回归范围

```
myenv/bin/python -m pytest tests/test_skills.py tests/test_signals.py tests/test_signals_mid.py tests/test_pipeline.py tests/test_agent.py tests/test_dashboard_signals.py -q
ruff check invest/signals invest/skills invest/pipeline.py dashboard tests/test_signals.py tests/test_signals_mid.py tests/test_dashboard_signals.py
```

**禁止** 为了过测试去改 a5/a6 输出格式。

---

## 阶段 F — 冻结与收尾

### Task F1: 冻结核对 ✅

确认日常表面 **没有新增**：

- 凯利/Wilson、簇预算、双 Agent 工单、周期镜像打分、BCS/VMS、比价主链、联动网络当选方向。

核对方法：

- `dashboard/app.py` `PAGES` 只有多「交易信号」，不新增多纪律页。
- 报告 `uses` 不新增 d7 以外的冻结栏目；a0/a3/a4/b1/a7 不引用 `kelly.py`。
- CHAT `TOOL_SCHEMAS` 保留原工具（点名仍可用）。

可写一个轻量测试：`grep` 或 assert `'kelly' not in a3_daily.py source`。

---

### Task F2: 文档 ✅

- `docs/plans/2026-09-09-辅助交易完善-design.md` 状态改为「实现中/已落地」。
- 本清单勾选。
- 可选：`AGENTS.md` 模块地图加一句 `invest/signals/` 短线+中线、仪表盘信号页。不要写长文。

---

### Task F3: 最终验收命令 ✅

```
myenv/bin/python -m pytest tests/test_signals.py tests/test_signals_mid.py tests/test_skills.py tests/test_pipeline.py tests/test_agent.py tests/test_dashboard_signals.py tests/test_freeze_assist.py -q
ruff check invest scripts tests dashboard
```

Expected: pytest 全过，ruff 0 error。

手工（不写自动化）：真实库 `init_db` 一次；盘后打开仪表盘总览散点 + 交易信号页能读到 `session=daily` 行。

---

## 关键实现细节（避免返工）

### 1. 生产路径不要搞错 persist

`JOB_FUNCS["after_close"]` = 16:00 采集+quant，**不发** a3。  
`JOB_FUNCS["evening_report"]` = 22:00 才 `run_structured("a3_daily")`。  
因此中线必须挂在 `quant()`；收盘短线必须挂在 evening/a3，而不是只改 `notify_after_close`。

### 2. b1 明确发现的硬条件

`layer=="discovery" and severity=="action"`。  
把 `lianban_promote`、破昨收缩量、高位放量滞涨、`sector_collective` 做成 action，否则盘中永远看不到池外。

### 3. 四象限互斥

单行业只进一个 quad_*，但四类规则都要实现、都要在 UI/报告分组里出现。

### 4. 发现层不是全市场

RS 核心股只来自 `industry_map.stocks_of` + 池内同行业。映射覆盖不全时宁缺毋滥。

### 5. Token

不新增定时 LLM、不把 max_tokens 当优化项改掉。只改 prompt 约束。

### 6. 旧 Signal() 构造

测试与 `format.py` 里手工 `Signal(...)` 可不写 horizon/layer。

---

## 建议 commit 切分（实现时由用户决定是否提交）

1. `feat(signals): trade_signals 增加 horizon/layer 与查询`（A）
2. `feat(signals): 中线四象限与 daily 落库`（B）
3. `feat(signals): 发现宇宙与短线规则扩展`（C）
4. `feat(dashboard): 交易信号页与坐标系复盘`（D）
5. `feat(reports): 盘前盘中盘后周报接入信号总线`（E）
6. `docs: 辅助交易完善方案落地说明`（F）

---

## 不做清单（实现时若想加，先停下来改方案）

- 自动交易、自动入池
- 每条信号 LLM、新增日报/周报 LLM 次数
- 全市场 daily_bars 循环
- B2 10 秒推送
- 改 a5/a6
- 仪表盘定时刷新
- 新 JOB_FUNCS 项
- 解冻凯利/双 Agent 到报告默认栏目
