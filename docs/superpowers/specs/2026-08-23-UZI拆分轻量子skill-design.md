# UZI 大 Skill 拆分 · 轻量子 Skill 体系设计

> 日期：2026-08-23 ｜ 分支：dev/2026-08-23
> 方法：grilling（两轮设计树对齐）+ brainstorming（architectural 路径）
> 范围：**将 UZI 深度分析的重型能力拆分为 9 个轻量子 skill，服务 agent 日常非报告对话**
> 代码实现留待本 spec 审阅通过后的实施步骤（Phase 1/2/3）

---

## 1. 背景与目标

### 1.1 背景
- UZI deep-analysis 是报告级重型流水线：22 维数据采集 + 66 评委 role-play + 机构建模（DCF/Comps/LBO）+ HTML 报告。
  日常对话中"分析一只票"用它属于杀鸡用牛刀：耗时 1-20 分钟、token 消耗大、产物（HTML 报告）超出对话所需。
- 飞书 Agent（CHAT_SYSTEM）目前只有 3 个方法论注入 skill（serenity / youzi / stock_analysis），
  且只有 `run_skill` 一条 UZI 流水线入口，**缺少"按角度轻量快答"的能力分层**。
- 用户诉求：把 UZI 的能力按角度拆成多个轻量子 skill（情绪面/技术面/基本面/周期/杀猪盘/板块/舆情/雪球大V），
  用于日常非报告对话；拆分结果中适合报告类的部分向用户提议。

### 1.2 目标（本阶段）
1. 建立 9 个轻量子 skill 的定义与文档骨架（`SKILL.md`），每个面向单一分析角度；
2. 建立**主角度判别机制**：按个股特征自动路由"该用哪个角度当主角度"（连板股→游资视角、周期股→周期视角等）；
3. 设计雪球大 V 数据模型（SQLite 表先行）+ 写入工具，为二期采集器铺路；
4. 明确与 UZI deep-analysis / 现有 3 方法论 / 报告类功能的边界，并提出报告类复用提议；
5. 交付本设计文档供评审。

### 1.3 非目标（明确不做）
- 不修改 UZI deep-analysis 本体；`run_skill` 深度报告入口保持不变；
- 不做二期社区采集器（cookie 登录雪球、定时任务）——只做表结构与数据缺口标注；
- 不实现代码（Phase 1/2/3 为评审通过后的实施计划，不在本 spec 内落码）；
- 不删除任何现有功能（serenity/youzi/stock_analysis 保留，新 skill 引用其方法论要点）。

---

## 2. 拆分原则（用户确认）

| 原则 | 内容 |
|---|---|
| 单角度 | 每个 skill 只回答一个分析角度，不做全景 |
| 轻量 | 每个 `SKILL.md` ≤ 2KB；单次回答工具调用 ≤ 2 次（延续 CHAT_SYSTEM 省 token 规则） |
| 组合 | 主角度 1 个 + 按需辅助角度最多 2 个；全面分析引导到 `run_skill` 深度报告 |
| 主角度判别 | 判别逻辑写进 skill；必要时单独建判别 skill（本次建 `angle-selector`） |
| 完整 8 信号 | 杀猪盘检测保留 UZI trap-detector 完整 8 信号（用户确认，接受相应 token 成本，每条信号最多 1 次搜索并复用结果） |
| 数据缺口如实标注 | 雪球站内反爬抓不到的数据标"数据不足"，不硬编 |
| 方法论复用 | 新 skill 引用/精炼现有 3 方法论要点，不重复维护两份 |

---

## 3. 架构设计

### 3.1 目录布局（新增 9 个 skill 文档）

```
.claude/skills/                    # 通用工程 skill 已有；新增分析子 skill
├── angle-selector/SKILL.md        # 主角度判别（新）
├── stock-emotion/SKILL.md         # 个股·情绪面
├── stock-technical/SKILL.md       # 个股·技术面
├── stock-fundamental/SKILL.md     # 个股·基本面
├── stock-cycle/SKILL.md           # 周期分析（情绪周期 + 行业/宏观周期）
├── trap-scan/SKILL.md             # 杀猪盘检测（完整 8 信号）
├── sector-analysis/SKILL.md       # 板块分析
├── opinion-analysis/SKILL.md      # 舆情分析（雪球/股吧/新闻）
└── big-v-monitor/SKILL.md         # 雪球大V监控与总结

invest/agent/
├── agents.py                      # CHAT_SYSTEM 增加"角度分析 skill 表"（触发词摘要，~500字）
├── tools.py                       # Phase 2：新增 big_v_update 工具注册（TOOL_SCHEMAS + dispatch）
└── skill_runner.py                # 不变（run_skill 仍指 UZI deep-analysis）

invest/db.py                       # Phase 2：SCHEMA_SQL 增加 big_v_profile / big_v_opinion
data/big_v_profiles.json           # 可选：半自动画像文件（与表并存的轻量缓存，Phase 2 前先用）
```

### 3.2 触发机制（关键约束：飞书对话是单轮 API，模型看不到文件）

- **判别表精简版 + 触发词摘要注入 CHAT_SYSTEM**（一行一个 skill：触发词 + 主角度条件 + 一句话方法论 + 数据工具组合），
  模型按语义自选激活并在回复末尾标注（沿用现有 `↘ 已使用 Skill：xxx` 机制）；
- **完整方法论（推理模板/输出格式/边界）放各 SKILL.md**，作为人读资产 + 未来工具化读取的载体；
- 新注入段控制在 ~500 字，避免系统提示膨胀。

### 3.3 与 UZI / 现有方法论边界

| 资产 | 定位 | 关系 |
|---|---|---|
| UZI deep-analysis（run_skill） | 深度报告级（22 维 + 66 评委 + HTML） | 子 skill 数据不足或用户要全面分析时引导至此 |
| UZI lhb-analyzer / trap-detector | 重数据版（脚本） | trap-scan 引用其 8 信号清单与评级表；龙虎榜深度仍走 lhb-analyzer |
| serenity / youzi / stock_analysis | 方法论文档 | 子 skill 引用其要点（如 stock-emotion 引用 youzi 情绪周期四阶段），不重复维护 |
| 9 个新子 skill | 日常对话快答 | 主角度判别 → 组合 → 输出，≤2 次工具调用 |

---

## 4. 子 Skill 定义（9 个）

统一 SKILL.md 骨架：`frontmatter（name/description/触发词）` + `主角度条件` + `数据工具组合` + `输出模板` + `Token 预算` + `失效条件`。

### 4.0 angle-selector · 主角度判别（新，编排入口）
- 触发：分析任何个股前的第一步（模型先判主角度再选 skill）；
- 判别表（精简版，注入 CHAT_SYSTEM）：

| 个股特征（优先自上而下命中） | 主角度 | 说明 |
|---|---|---|
| 连板 ≥2 / 涨停池成员 / 高换手题材股 | stock-emotion（情绪/游资） | 连板股第一性原理是情绪与资金博弈 |
| 周期行业（有色/煤炭/化工/钢铁/航运/养殖/地产链） | stock-cycle | 周期股看库存/价格/景气度位置，不看静态 PE |
| 破净 / 高股息 / 低 PE 分位 / 消费白马 | stock-fundamental | 价值与财务质量主导 |
| 创新高放量 / 破位下跌 / 平台突破 / 次新 | stock-technical | 趋势与位置主导 |
| 题材龙头 / 板块联动强 / 同板块多股异动 | sector-analysis | 先看板块再看个股 |
| 小盘次新 + 消息密集 + 老师带/群推语境 | trap-scan（辅助优先） | 杀猪盘风险优先于角度分析 |
| 机构重仓 / 大市值 / 财报密集期 | stock-fundamental + sector-analysis | 双主角度 |

- 每个子 skill 的 SKILL.md 均含"我何时是主角度"自述段，供判别表双向引用；
- 完整版（含推理模板、反例、边界 case）放 `angle-selector/SKILL.md`。

### 4.1 stock-emotion · 个股·情绪面
- 触发词：连板/打板/炸板/接力/情绪/游资/龙头/能不能追；
- 主角度条件：连板 ≥2、涨停池成员、高换手、题材热度高；
- 数据工具：`query_stock_daily`（近 60 日）+ `query_temperature`（市场温度）+ 涨停池/炸板池数据；
- 输出模板：情绪周期定位（冰点/启动/主升/退潮）→ 个股在周期中的位置 → 连板高度与梯队 → 一句话结论；
- Token 预算：≤150 字；失效条件：量能/连板断板/情绪转冷。

### 4.2 stock-technical · 个股·技术面
- 触发词：技术面/K线/均线/支撑/压力/MACD/买点/卖点；
- 主角度条件：创新高放量、破位、平台突破、次新、无明显题材；
- 数据工具：`query_stock_daily`（窗口 60/250 按周期）；
- 输出模板：趋势阶段（上升/下跌/震荡）→ 均线排列 → 关键支撑压力位（带价格）→ 量能确认 → 结论；
- Token 预算：≤120 字；失效条件：跌破 X 元/放量滞涨。

### 4.3 stock-fundamental · 个股·基本面
- 触发词：基本面/财报/估值/值不值得长期拿/排雷/ROE/PE/PB；
- 主角度条件：破净、高股息、低估值分位、消费白马、机构重仓；
- 数据工具：`cross_validate`（估值分位/拥挤度）+ `web_search`（最新财报/公告，1 次）；
- 方法论引用：stock-analysis 五步法（财务排雷/市值倒推/反证清单）；
- 输出模板：财务质量一句话 → 估值分位 → 排雷红警（如有）→ 关键跟踪因子 → 结论；
- Token 预算：≤180 字；失效条件：财报暴雷/逻辑破坏。

### 4.4 stock-cycle · 周期分析（情绪周期 + 行业/宏观周期）
- 触发词：周期/景气度/库存/价格/拐点/宏观/利率；
- 主角度条件：周期行业（有色/煤炭/化工/钢铁/航运/养殖/地产链）、或用户明确问周期位置；
- 数据工具：`query_macro`（宏观流动性）+ `query_rotation`（板块轮动位置）+ `cross_validate`（行业）；
- 双尺度：短线情绪周期（四阶段，引用 youzi）+ 中线行业/宏观周期（景气度/库存/价格位置，引用 serenity）；
- 输出模板：两个时间尺度各自定位 → 当前处于什么位置 → 对个股的传导 → 结论；
- Token 预算：≤200 字；失效条件：价格/库存数据反转。

### 4.5 trap-scan · 杀猪盘检测（完整 8 信号）
- 触发词：杀猪盘/老师带/群里推荐/内幕/稳赚/安不安全/被套路；
- 主角度条件：小盘 + 消息密集 + 推荐语境；用户要求检测时无条件触发；
- 8 信号（完整保留，来自 UZI trap-detector）：① 低质账号同推 ② 话术模板化 ③ 付费社群引流 ④ 基本面与热度脱节 ⑤ K线异常配合 ⑥ 老师/股神人设 ⑦ 跨平台联动 ⑧ 虚假研报；
- 执行：硬信号（④⑤）用本地数据（`query_stock_daily` + `cross_validate`）；软信号（①②③⑥⑦⑧）`web_search` 每条最多 1 次、复用同一批搜索结果，搜不到标"数据不足"不硬判；
- 输出：命中信号清单（含证据 URL）→ 🟢🟡🟠🔴 评级 → 建议（≥4 信号必须"强烈建议谨慎/回避"开头）；
- Token 预算：≤250 字；失效条件：无（输出即结论，注明数据不足项）。

### 4.6 sector-analysis · 板块分析
- 触发词：板块/行业/题材/能不能做/主线/轮动/龙头；
- 主角度条件：题材龙头、同板块多股异动、用户问行业；
- 数据工具：`query_strength`（行业强度）+ `query_rotation`（轮动）+ `query_capital`（资金属性）+ `cross_validate`（行业，多源共振）；
- 输出模板：板块强度/轮动位置 → 资金净流入 → 龙头与联动 → 与大盘温度匹配度 → 结论；
- Token 预算：≤180 字；失效条件：资金流出/强度回落。

### 4.7 opinion-analysis · 舆情分析（雪球/股吧/新闻）
- 触发词：舆情/雪球/股吧/东财/评论区/都在说/舆论/热度；
- 主角度条件：用户问"市场怎么议论"、消息密集期、事件驱动；
- 数据工具：`web_search`（1 次，`site:xueqiu.com {代码}` / `site:guba.eastmoney.com {代码}` / 新闻）+ `web_fetch`（可选，抓可达页面）；
- 输出：平台分布 → 情绪倾向（多/空/分歧）→ 典型观点 2-3 条（带来源）→ 热度异常提示；
- 数据缺口：雪球站内完整言论反爬抓不到 → 标"站内数据不足，仅搜索摘要可见部分"；
- Token 预算：≤180 字；失效条件：无（注明数据不足）。

### 4.8 big-v-monitor · 雪球大V监控与总结
- 触发词：某大V名字/这个老师靠谱吗/XX 最近观点/XX 胜率/XX 风格；
- 主角度条件：用户点名具体大 V；引用"某大V说"时辅助触发；
- 数据：`web_search`（`雪球 {ID} 观点/持仓/历史`）+ `big_v_update` 工具（Phase 2，读写 SQLite 表）；
- 输出：画像卡（风格/擅长方向/自述胜率/主页）→ 最近观点 2-3 条（带链接）→ 历史观点一致性 → 结论（值得参考/仅娱乐/风险提示）；
- Token 预算：≤200 字；失效条件：标注数据截至时间。

### 4.9 大 V 数据模型（SQLite 表先行，Phase 2 实现）

```sql
CREATE TABLE IF NOT EXISTS big_v_profile (
    id          TEXT PRIMARY KEY,          -- slug，如 xq_duanyp
    name        TEXT NOT NULL,             -- 显示名
    platform    TEXT NOT NULL DEFAULT 'xueqiu',
    xueqiu_id   TEXT,                      -- 雪球用户 ID
    homepage    TEXT,                      -- 主页 URL
    style       TEXT,                      -- 风格标签：价投/成长/游资/宏观/量化/技术/趋势
    strengths   TEXT,                      -- 擅长方向（行业/赛道）
    win_rate    TEXT,                      -- 自述/公开胜率（文本，注明口径）
    track_record TEXT,                     -- 历史战绩/里程碑（文本或 JSON）
    source_links TEXT,                     -- 公开资料链接（JSON 数组）
    notes       TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS big_v_opinion (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id   TEXT NOT NULL REFERENCES big_v_profile(id),
    opinion_date TEXT NOT NULL,            -- 观点发表日
    symbol       TEXT,                     -- 涉及标的（空=大盘/行业）
    topic        TEXT,
    view         TEXT NOT NULL,            -- 观点内容
    bias         TEXT,                     -- bullish / bearish / neutral
    confidence   REAL,                     -- 0-1，可空
    url          TEXT,                     -- 原文链接
    collected_at TEXT DEFAULT (datetime('now','localtime'))
);
```

- 写入：新增 `big_v_update` 工具（TOOL_SCHEMAS + dispatch + `storage.upsert_df`），agent 搜索后调用写入；
- 读取：`query_big_v`（可选，Phase 2 一并做）；
- 二期：cookie 登录采集器定时写入两表（合并舆情采集为"社区采集"项目）。

---

## 5. 组合规则（日常对话执行流）

1. **判别**：先按 angle-selector 判别表定位主角度（1 个）；用户语境带明显信号（"老师带"等）时 trap-scan 无条件升为主角度；
2. **组合**：主角度 + 最多 2 个辅助角度；工具调用 ≤2 次，多个角度共享同一批工具结果（如 query_stock_daily 一次取数供情绪/技术共用）；
3. **输出**：每角度 ≤1 行结论 + 总评一句；末尾标注 `↘ 已使用 Skill：xxx`；
4. **升级**：用户要求"全面/深度/出报告" → 引导 `run_skill`（提示约 1 分钟）。

---

## 6. 报告类复用提议（拆分成果反向输血报告）

| 子 skill | 报告复用点 | 说明 |
|---|---|---|
| opinion-analysis | 日报/盘中报告·消息面小节（D1） | 替换/增强现有消息面 LLM 提炼，聚合社区舆情 |
| sector-analysis | 日报·资金主线（D13）/板块异动（D14） | 板块强度+资金+龙头联动成段 |
| stock-emotion | 盘中情绪快报·情绪人气（D11）/连板梯队（D12） | 情绪周期定位进报告 |
| trap-scan | 候选池/持仓个股预警扫描（可定时） | 对候选池逐股扫 8 信号出预警清单 |
| stock-cycle | 周报·行业景气段 | 双尺度周期定位进周报 |
| big-v-monitor | 日报可选段"大V观点" | 画像沉淀后按需展示 |

（此部分为提议：是否接入由后续报告类项目评审决定，不在本 spec 实施。）

---

## 7. 实施步骤（评审通过后执行）

### Phase 1 · skill 文档与触发（轻量，先行）
1. 新建 9 个 `.claude/skills/<name>/SKILL.md`（按 §4 骨架）；
2. `invest/agent/agents.py`：CHAT_SYSTEM 增加"角度分析 skill 表"段（~500 字：触发词 + 主角度条件 + 数据工具 + 标注格式）；
3. 更新 AGENTS.md「通用工程 Skill」一节登记新 skill；
4. 测试：`test_agents.py` 校验 CHAT_SYSTEM 触发词段存在且标注格式不变；`ruff check`。

### Phase 2 · 大 V 数据层
1. `invest/db.py`：SCHEMA_SQL 追加两表；跑 `init_db` 生效；
2. `invest/agent/tools.py`：新增 `big_v_update`（写入/更新 profile+opinion）与 `query_big_v`（读取），注册 TOOL_SCHEMAS + dispatch；
3. `data/big_v_profiles.json` 半自动画像缓存（Phase 1 期间先用）；
4. 测试：新表 upsert/查询测试（全 mock）+ 工具注册测试；`ruff`、`mypy`。

### Phase 3 · 报告类复用（可选，另行立项）
- 按 §6 逐项接入 report.py 小节；对齐"报告模板 skill 化"项目（2026-08-22 spec）的小节边界。

### 二期规划（不属本 spec）
- 社区采集器：雪球 cookie 登录 + 用户页/动态 API + 定时任务写入 big_v_* 与舆情表；
- 舆情分析升级：雪球站内数据补齐，"数据不足"标注移除。

---

## 8. 自审清单

- [x] 无占位符/TODO 未决：全部决策已与用户两轮 grilling 对齐（载体/大V数据/周期范围/清单/交付/组合/8信号/舆情/存储）；
- [x] 内部一致：§2 原则 ↔ §4 skill 定义 ↔ §5 组合规则 ↔ §7 实施步骤一一对应；
- [x] 范围聚焦：只做拆分设计 + 表结构 + 工具接口设计，不落码、不动 UZI 本体；
- [x] 歧义已消解："主角度判别"明确为 angle-selector + 各 skill 自述段 + CHAT_SYSTEM 精简表三层；
  "完整 8 信号"明确为信号全覆盖 + 单信号最多 1 次搜索 + 结果复用 + 数据不足标注；
- [x] 与既有资产边界：run_skill / serenity / youzi / stock_analysis / 报告 skill 化项目均不冲突。

---

## 9. 待用户确认项（评审本 spec 时逐项确认）

1. 9 个 skill 命名与划分是否合意（尤其 angle-selector 单独成 skill）；
2. CHAT_SYSTEM 注入 ~500 字触发词表可接受（token 增量）；
3. 大 V 两表 schema 与 `big_v_update`/`query_big_v` 工具设计是否合意；
4. §6 报告类复用提议哪些立项接入；
5. Phase 1/2/3 实施顺序与本期范围（先做 Phase 1？还是 1+2？）。
