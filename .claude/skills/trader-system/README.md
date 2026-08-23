---
name: trader-system-skills
description: traderSystem 自沉淀 skill 集（2026-08-22）：盘前/盘中/盘后报告方法论 + ETF 分析 + 预案推演 + 观点复盘。当用户问"报告怎么生成/预案怎么推/ETF 怎么分析"时参考。
---

# traderSystem 沉淀 skill 集

本目录是 traderSystem（A 股投资系统）在飞书体验改造过程中沉淀的方法论资产，
由实际运行的报告 pipeline（`invest/skills/`）固化而来，供 Claude/agent 与后续迭代参考。

## 结构

| skill | 对应系统模块 | 沉淀要点 |
|---|---|---|
| `premarket-report/` | `a0_premarket` | 盘前报告：隔夜外围（含日韩）+ LLM 解读 + 涨停异动监控 + 消息汇总 |
| `intraday-report/` | `b1_intraday` | 盘中报告：盘面总览 + 情绪预测 + 日内主线（ETF+推荐股）+ 核心关注/预案对照 |
| `evening-report/` | `a3_daily` | 盘后日报（盘中 PLUS）：ETF 验证 + 观点复盘 + 板块总分析 + 预案闭环 |
| `etf-analysis/` | `invest/data/etf.py` | ETF 三因子强度验证（量比/超大单/主力净流入）≈ 大资金信号 |
| `plan-review/` | `_daily_llm.plan_gen/plan_review` | 明日预案 5 要素 + 质量复盘 3 步 + 持续迭代 |
| `viewpoint-review/` | `_daily_llm.intraday_review` | 盘中观点对错复盘 + 错误分类 + 经验沉淀 |

## 核心设计原则（贯穿全部）

1. **数据新鲜度防守**：报告/观点前查 `query_data_freshness`，滞后先说明原因；
2. **禁止编造**：LLM 只许基于给定数据推理（prompt 硬约束），失败回退规则/直列；
3. **LLM 记账**：所有调用按 job 记 `llm_usage`（intraday_report / daily_report），超限告警；
4. **纯函数渲染**：skill `render()` 无副作用；落库（观点/预案）由发送层完成；
5. **预案闭环**：盘中观点落 `viewpoints source='intraday_report'` → 盘后复盘；
   盘后预案落 `source='plan'` → 次日/盘中对照 → 质量复盘 → 迭代本 skill。
