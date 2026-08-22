# 报告模板 Skill 化改造 · 设计文档

> 日期：2026-08-22 ｜ 分支：dev/2026-08-22
> 方法：grilling（两轮设计树对齐）+ brainstorming（architectural 路径）
> 范围：**只做结构 skill 化，不改任何报告内容；输出与现状逐字节一致**
> 代码实现留待本 spec 审阅通过后的 writing-plans / 实施步骤

---

## 1. 背景与目标

### 1.1 背景
报告模板目前集中在 `invest/report.py`（1070 行）+ `invest/pipeline.py` + `invest/scheduler.py` 内，
是一堆平铺函数（`daily_report` / `premarket_report` / `morning_brief_report` / `weekly_report` /
`intraday_report` 及 20+ 个内嵌小节函数），无统一契约、无注册表、无法按报告/小节独立配置与测试，
也是"逐个指定改造模板"时缺少锚点的主要原因。

### 1.2 目标（本阶段）
1. 建立统一的**报告 Skill 引擎**：目录 + 轻量声明式契约 + 注册表 + Runner；
2. A/B 组报告与 D 组小节全部组织为 skill（薄包装现有实现）；
3. **输出与现状逐字节一致**——现有测试断言全部保持通过；
4. 为后续"逐个指定内容改造"提供稳定的 skill 边界。

### 1.3 非目标（明确不做）
- 不改任何报告文案/排版/数据逻辑（Q4：逐字节一致）；
- 不引入 LLM 触发报告（Q7：报告 skill 不暴露给 Agent 的 `run_skill` 工具）；
- 不迁移 report.py 实现体（Q9：report.py 原样保留，skill 薄包装）；
- 不删除除 A7 外的任何现有功能。

## 2. 改造范围

### 2.1 纳入 Skill 化（30 个）
| 组 | skill 数 | 清单 |
|---|---|---|
| 报告 skill（kind=report） | 7 | A1 盘前清单 / A2 盘前信息早报 / A3 盘后日报 / A4 周报 / A5 月度复盘推送 / A6 年度复盘推送 / B1 盘中实时报告 |
| 小节 skill（kind=section） | 23 | D1 消息面 / D2 重点行业 / D3 风格 / D4 强度榜 / D5 涨跌榜 / D6 宏观 / D7 Agent 观点 / D8 温度倾向 / D9 评级仓位 / D10 操作建议 / D11 情绪人气 / D12 连板梯队 / D13 资金主线 / D14 板块异动 / D15 龙虎榜龙头 / D16 持仓警戒 / D17 候选池变化 / D18 异常波动 / D19 做T / D20 建仓时机 / D21 数据截至 / D22 评级 / D23 涨跌家数 |

### 2.2 排除（保持脚本不动，纯提示信息类）
A8 环境重评触发（Q2 并入）、A9 收盘扫描 [P1]（Q2 并入）、A10 任务失败告警、
A11 盘后报告数据滞后提示、B2 盘中异动推送、B3 P0 监控告警、B4 LLM 用量告警、C 组全部（C1–C8）。

### 2.3 删除
**A7 P2 例行简报**（Q5 选 B）：
- `invest/pipeline.py` 的 `notify_p2_brief` 函数删除；
- `tests/test_todo_a.py` 中对该函数的引用与断言移除；
- `docs/报告模板改造清单.md` 中 A7 条目更新为"已删除"。
- 依据：内容与 A3 重复、当前无任何定时任务调度（22:00 合并版已取代），属遗留模板。

## 3. 架构设计

### 3.1 目录布局（新增 `invest/skills/`）
```
invest/skills/
├── __init__.py
├── contract.py      # SKILL 元数据与参数校验（轻量，无基类）
├── registry.py      # 显式注册表：get() / list() / validate_all()
├── runner.py        # run(skill_id, **params) -> str（最小职责）
├── reports/         # 7 个报告 skill
│   ├── a1_premarket.py
│   ├── a2_morning_brief.py
│   ├── a3_daily.py
│   ├── a4_weekly.py
│   ├── a5_monthly.py
│   ├── a6_yearly.py
│   └── b1_intraday.py
└── sections/        # 23 个小节 skill
    ├── d1_news_block.py
    ├── d2_focus_industries.py
    ├── ...（d3–d22）
    └── d23_breadth.py
```

### 3.2 Skill 契约（contract.py）
每个 skill 文件导出两个成员（**声明式元数据 + 纯函数 render**，不引入基类）：

```python
SKILL = {
    "id": "a3_daily",
    "name": "盘后日报",
    "kind": "report",            # report | section
    "description": "22:00 盘后日报（合并 daily_report + 今日统计 + 数据质量）",
    "uses": ["d1_news_block", "d2_focus_industries", "d7_agent_viewpoints", ...],
    "params": {                   # 说明性参数清单（校验用），默认值标记可选
        "db_path": "str, required",
        "agent_text": "str, optional, default ''",
    },
}

def render(**params) -> str:
    """生成报告/小节文本。实现为现有函数的薄包装，输出逐字节一致。"""
```

**约定**：
- `render` 必须是纯函数（无副作用：不落库、不推送、不写 job_runs）；副作用全部留在调用方；
- 参数校验：`registry.run` 前由 runner 依据 `SKILL["params"]` 做缺失/多余检查；
- **异常不上抛给用户文案**：runner 不做字符串兜底（见 3.4 错误处理）。

### 3.3 注册表（registry.py）
- **显式注册**（非自动扫描）：`REGISTRY: dict[str, Skill]`，按 id 登记全部 30 个 skill；
- 提供 `get(skill_id)`（未知 id 抛 `KeyError`）、`list(kind=None)`、`validate_all()`（id 唯一、
  kind 合法、uses 引用的 skill id 存在、params 描述合法）；
- 报告 skill 的 `uses` 指向小节 skill，用于文档与一致性校验（**不强制**报告内部必须经 runner
  调小节——见 3.5）。

### 3.4 Runner（runner.py）——最小职责（Q8）
`run(skill_id, **params) -> str`：
1. `get(skill_id)` 查注册表（未知 id → `KeyError`，调用方现有 try 已覆盖，行为与现状一致）；
2. 按 `SKILL["params"]` 校验参数（缺失必填 / 传入未声明参数 → `TypeError`）；
3. 调用 `skill.render(**params)`，**异常原样上抛**；
4. **不做**：字符串兜底文案、限频、job_runs 留痕、推送——全部保留在现有调用方
   （scheduler `_wrap`、feishu_ws `_build_intraday_report` 的 try/except 现状不变）。

> 为何不吞异常：现状下调度路径异常由 `_wrap` 记为 failed 并推送"任务失败"；飞书路径由
> `_build_intraday_report` 兜底为"[报告生成失败: ...]"。runner 吞异常会让两条路径行为漂移，
> 违反"逐字节一致"。

### 3.5 薄包装与依赖方向（Q9）
- 报告 skill 的 `render` 内部**直接调用 `invest/report.py` 现有函数**
  （如 `a3_daily.render` → `report.daily_report(db_path, agent_text)`），不改其实现；
- 小节 skill 的 `render` 薄包装 `report.py`/`pipeline.py` 对应函数（见 §4 映射表）；
- 依赖方向**单向**：`skills → report.py / review / quant`，`report.py` **不** import `skills`
  （避免循环依赖，符合 AGENTS.md 懒加载约定）；
- 报告 skill 内部按现状传参（conn / live / pct_map / score 等上下文参数由报告 skill 自行
  组织），`uses` 仅作声明，不做强制路由——保证与现状行为零差异。

### 3.6 A5/A6 特殊处理
- `a5_monthly.render(db_path)`：内部 `connect(db_path)` → `monthly_review(conn)` →
  组装**推送摘要文本**（现状 `scheduler._monthly` 内的"月度复盘: 观点命中率…"文案原样迁入）；
  `save_report` 落库动作**留在调度器**（render 纯函数无副作用）；
- `a6_yearly.render(db_path)`：同上，组装"年度复盘已生成: N 组回测结论待检视"。

### 3.7 B1 特殊处理
- `b1_intraday.render(db_path, public=False, brief=True)` → `report.intraday_report(...)`；
- 3800 字截断（`MAX_REPORT_LEN`）与 30s 限频**留在 feishu_ws**（调用方），skill 不做截断。

## 4. Skill 映射表（30 个）

### 报告 skill（7）
| id | 名称 | 实现（薄包装） | 主要 uses |
|---|---|---|---|
| a1_premarket | 盘前清单 | `report.premarket_report(db_path, agent_text)` | d3,d8,d9,d21,d22 |
| a2_morning_brief | 盘前信息早报 | `report.morning_brief_report(db_path)` | d5,d6,d8,d9,d15,d18,d21 |
| a3_daily | 盘后日报 | `report.daily_report(db_path, agent_text)` | d1,d2,d3,d4,d6,d7,d8,d9,d10(内嵌),d16,d17,d18,d20,d21,d22 |
| a4_weekly | 周报 | `report.weekly_report(db_path, agent_text)` | d1,d6,d7,d8,d9,d21,d22 |
| a5_monthly | 月度复盘推送 | `monthly_review` + 摘要组装 | — |
| a6_yearly | 年度复盘推送 | `yearly_review` + 摘要组装 | — |
| b1_intraday | 盘中实时报告 | `report.intraday_report(db_path, public, brief)` | d4,d8,d9,d10,d11,d12,d13,d14,d15,d16(条件),d18(条件),d19(条件),d20(条件),d21 |

### 小节 skill（23）
| id | 名称 | 薄包装实现 |
|---|---|---|
| d1_news_block | 消息面提炼 | `report._news_block(db_path, n, days, job)` |
| d2_focus_industries | 重点关注行业 | `report._focus_industries_block(conn, db_path)` |
| d3_style | 市场风格 | `report._style_block(conn)` |
| d4_strength | 短线/中线强度榜 | `report._strength_block(conn, period, n)` |
| d5_movers | 当日涨跌榜 | `report._movers_block(conn, n)` |
| d6_macro | 宏观流动性 | `report._macro_text(conn)` |
| d7_agent_viewpoints | Agent 复盘/周度观点 | `report._agent_viewpoints(conn, n)` |
| d8_temp_guide | 温度→倾向 | `report._temp_guide(score)` |
| d9_rating_guide | 评级→仓位上限 | `report._rating_guide(conn)` |
| d10_action_guide | 今日操作建议 | `report._action_guide(conn, score)` |
| d11_emotion | 情绪·人气 | `report._emotion_block(conn)` |
| d12_limit_up_ladder | 连板梯队 | `report._limit_up_ladder_block(conn, n)` |
| d13_fund_line | 资金主线 | `report._fund_line_block(conn, n)` |
| d14_sector_moves | 板块异动 | `report._sector_moves_block(conn, n)` |
| d15_capital_leaders | 龙虎榜龙头 | `report._capital_leaders_block(conn, n)` |
| d16_card_alerts | 持仓警戒 | `report._card_alerts(conn, live_prices)` |
| d17_pool_delta | 候选池变化 | `report._pool_delta(conn)` |
| d18_abnormal_moves | 异常波动 | `report._abnormal_moves(conn, n)` |
| d19_t_trade_hints | 做 T 提示 | `report._t_trade_hints(conn, live, pct_map)` |
| d20_entry_timing | 建仓时机 | `report._entry_timing_hints(conn)` |
| d21_freshness | 数据截至 | `report._freshness(conn)` |
| d22_ratings | 评级块 | `report._ratings_block(conn)` |
| d23_breadth | 涨跌家数 | `report._breadth(conn)` |

> 注：小节 skill 的 params 与 `render` 签名按上表实现函数对齐（conn 由调用方传入或
> 小节自行 `connect(db_path)`——沿用现状调用方式，保证逐字节一致）。

## 5. 调用方改造（Q7：只改现有调用方，机械替换）

| 调用方 | 现状 | 改为 |
|---|---|---|
| `pipeline.notify_premarket` | `premarket_report(db_path, agent_text)` | `skills.runner.run("a1_premarket", db_path=..., agent_text=...)` |
| `pipeline.notify_morning_brief` | `morning_brief_report(db_path)` | `runner.run("a2_morning_brief", db_path=...)` |
| `pipeline.notify_after_close` | `daily_report(db_path, agent_text)` | `runner.run("a3_daily", ...)` |
| `pipeline.notify_weekend` | `weekly_report(db_path, agent_text)` | `runner.run("a4_weekly", ...)` |
| `scheduler._monthly` | `monthly_review(conn)` + 摘要组装 + `save_report` | `runner.run("a5_monthly", db_path=...)` + `save_report`（落库保留） |
| `scheduler._yearly` | `yearly_review(conn)` + 摘要组装 + `save_report` | `runner.run("a6_yearly", db_path=...)` + `save_report`（落库保留） |
| `scheduler._evening_report` | `daily_report(db)` + 【今日】统计 + 数据质量 | `runner.run("a3_daily", ...)` 后由调度器追加【今日】统计与数据质量（**拼接保持现状**） |
| `feishu_ws._build_intraday_report` | `intraday_report(db, public, brief)` + 3800 截断 | `runner.run("b1_intraday", db_path=..., public=..., brief=...)` + 截断保留 |

**约束**：
- `scheduler.py` 的 cron 注册、`_wrap` 留痕/失败推送、限频全部不动；
- `feishu_ws._agent_reply` 的分流/限频/意图识别不动；
- pipeline notify_* 函数签名不变（外部调用者零改动）。

## 6. 数据流

```
APScheduler / 飞书事件
   │
   ▼
scheduler._monthly / pipeline.notify_* / feishu_ws._build_intraday_report
   │  （副作用层：job_runs 留痕、限频、截断、Notifier/飞书发送 全部保留在这里）
   ▼
skills.runner.run(skill_id, **params)
   │  1) registry.get(id)   2) params 校验   3) render()
   ▼
skill.render(**params)  ──薄包装──►  report.py 现有函数 / review 模块
   │
   ▼
文本 str ──► 返回调用方 ──► 推送
```

## 7. 错误处理

| 场景 | 行为（与现状一致） |
|---|---|
| 未知 skill id | `KeyError` 上抛 → 调度路径 `_wrap` 记 failed + 推送任务失败；飞书路径现有 try 兜底 |
| 参数缺失/多余 | `TypeError` 上抛（编程错误，不静默） |
| render 异常 | 原样上抛，由调用方按现状处理（调度=失败留痕；飞书="[报告生成失败: ...]"） |
| LLM 小节失败（d1/d2 内部） | 已有内部回退逻辑（直列素材 / 只出数据），skill 不新增处理 |

## 8. 测试策略

### 8.1 新增 `tests/test_skills.py`
1. **注册表完整性**：30 个 skill 全注册；id 唯一；kind ∈ {report, section}；报告数量 7、小节 23；
   `uses` 引用的 id 均存在（`validate_all()` 通过）；
2. **逐字节一致**（复用现有测试 fixture/临时库模式）：
   - `runner.run("a1_premarket", ...) == report.premarket_report(...)`（同一临时库、同参数）；
   - a2 / a3 / a4 / b1 同上；
   - a5 / a6 输出 == 现状 scheduler 组装文本（构造 review 数据后比对）；
   - 抽查小节 skill：d1 / d2 / d8 / d21 等 `render` 输出 == 对应 report 函数输出；
3. **错误路径**：未知 id → KeyError；缺必填参数 → TypeError。

### 8.2 现有测试
- `test_report.py` / `test_report_short.py` / `test_morning_brief.py` / `test_pipeline.py`
  / `test_feishu_ws.py` 等**全部保持通过**（薄包装不改变输出）；
- `test_todo_a.py`：移除 A7 `notify_p2_brief` 相关用例；
- 全量 `pytest tests` + `ruff check invest tests` 0 错误。

## 9. 实施步骤（供 writing-plans 细化）
1. 建 `invest/skills/` 骨架：`contract.py`（SKILL 元数据 + 校验）、`registry.py`（显式注册）、`runner.py`；
2. 迁移 D 组：23 个 `sections/d*.py` 薄包装（含单元测试）；
3. 迁移 A/B 组：7 个 `reports/*.py` 薄包装（A5/A6 摘要组装迁入，save_report 留在调度器）；
4. 接线调用方：pipeline notify_*、scheduler `_monthly/_yearly/_evening_report`、feishu_ws `_build_intraday_report`；
5. 删除 A7：`notify_p2_brief` + `test_todo_a.py` 引用 + 清单文档标注；
6. `tests/test_skills.py` 落齐，跑全量 pytest + ruff；
7. 更新 `docs/报告模板改造清单.md`（A/B/D 组标注"已 skill 化"，A7 标注已删除）；
8. 提交到 `dev/2026-08-22` 分支（不推 main）。

## 10. 风险与缓解
| 风险 | 缓解 |
|---|---|
| 逐字节一致被破坏 | runner/render 层零字符串加工，纯透传；测试 8.1-2 用断言锁定 |
| 循环依赖（skills ↔ report） | 依赖单向（skills → report/review），report.py 不 import skills；懒加载沿用 |
| A5/A6 文案迁移偏差 | 迁移后立即比对现状输出（测试 8.1-2） |
| 30 个文件批量改动引入噪音 | 每个 skill 文件极小（~15 行），按 D→A/B→接线分三步提交 |
| A7 删除遗漏引用 | 删除后 grep `notify_p2_brief` 确认零引用，再跑全量测试 |
