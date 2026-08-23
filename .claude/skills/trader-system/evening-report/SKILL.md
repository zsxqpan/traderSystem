---
name: evening-report
description: 盘后日报方法论（a3_daily，盘中报告 PLUS 版）。22:00 推送，4 点：盘面总览（含 ETF 分析）/盘中观点复盘/重要板块总分析（ETF 验证）/明日预案+质量复盘；尾部保留持仓警戒/消息面/候选池变化。
---

# 盘后日报（a3_daily）

## 触发
- 每日 22:00 `evening_report` 任务；数据新鲜度门禁（日线/指数未到最近交易日 → 不发报告只推原因）
- scheduler 追加【今日】到期进复盘/工单/新增观点 + 数据质量（PIT 四状态）

## 结构（4 点 + 尾部）
1. **盘面总览（收盘角度）**：指数表格 + **指数 ETF 表格**（涨跌幅/成交额/换手/量比/主力净流入/超大单）
   + 大资金进出信号（指数 ETF 明显放量/超大单大额 ≈ 国家队动作）
2. **盘中观点复盘（LLM）**：当日 `viewpoints source='intraday_report'` 的观点（预测/建议/短线判断）
   vs 当日实际（指数/板块涨幅）→ {verdict 对错, wrong_reasons 错误原因, lessons 经验}
   - 错误原因分类沉淀 → 可固化 viewpoint-review skill
3. **重要板块总分析（LLM + ETF 验证）**：固定 8 方向（AI硬件/AI软件/机器人/金融/金属/新能源/
   旧能源/内需），每方向对应代表 ETF（纯度高于板块指数）：
   - active=true：驱动/内部结构/ETF 验证/龙头，50 字内
   - active=false：一句话中线状态（横盘待变盘/持续阴跌未见底），避免过度分析
   - 方向无变化但有个股异动 → 一句话归因
4. **明日预案（LLM）+ 质量复盘**：
   - 生成：{direction 明日主线, picks 推荐介入(≤3只), plans 关注/持仓股操作预案}
   - 落库 `viewpoints source='plan'`（conclusion=JSON）
   - 质量复盘：近 N 日 plan vs 实际（推荐股次日涨跌比对）→ {quality, fixes} 迭代预案推演方式

**尾部保留**：持仓警戒（cards 收盘价 vs 止损/目标）/ 消息面（LLM 提炼近 2 日）/ 候选池变化

## 数据源
- 指数/ETF：同盘中（收盘后返回收盘数据）；持仓：cards 表；关注：candidate_pool core
- 预案历史：viewpoints source='plan'（created_at 日期 → 次日交易日 daily_bars 涨跌）

## LLM（job='daily_report'，4 次调用，失败回退直列）
1. intraday_review_llm（复盘）
2. board_analysis_llm（板块，max_tokens 1800）
3. plan_gen_llm（预案，picks 优先个股含 6 位代码，plans 覆盖全部持仓）
4. plan_review_llm（质量复盘）

## 预案闭环（核心资产）
```
B1 盘中观点 ──落库──> viewpoints source='intraday_report' ──> A3 点2 复盘
A3 点4 预案 ──落库──> viewpoints source='plan' ──> B1 点4 对照 + A3 次日质量复盘
质量复盘 fixes ──> 迭代 plan-review skill
```

## 删除项（由盘中/本报告新结构取代）
重点行业意见 / 强度榜 / 异常波动 / 建仓时机 / Agent 复盘节 / 龙虎榜 / 指数强弱榜 / 板块主线
