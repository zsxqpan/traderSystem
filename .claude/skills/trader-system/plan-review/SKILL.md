---
name: plan-review
description: 明日预案推演 + 质量复盘方法论（_daily_llm.plan_gen_llm / plan_review_llm）。盘后日报点4：基于盘面总结生成介入推荐与持仓操作预案（5 要素），落库 source='plan'；次日复盘预案质量（3 步），改进推演方式。本 skill 是本系统持续迭代的核心资产。
---

# 明日预案推演与质量复盘

## 触发
- **生成**：盘后日报点4（每日 22:00），输入=今日盘面总结（指数/ETF/板块）+ 关注/持仓股
  + 近 N 日预案历史；
- **复盘**：同一点4，输入=最近 N 日 `viewpoints source='plan'` 预案 vs 其后实际表现。

## 预案生成（5 要素）
输出 JSON 落库 `viewpoints source='plan'`（conclusion=JSON，status='active'）：
```json
{
  "direction": "明日主线方向判断（25字内）",
  "picks": [{"name": "可介入标的", "symbol": "6位代码", "reason": "理由20字内", "plan": "触发条件20字内"}],
  "plans": [{"symbol": "关注/持仓股代码", "action": "明日操作预案（持有/减/加/止损位）"}]
}
```
- `direction`：由点1-3 推导（指数/ETF 验证 + 板块活跃度 + 情绪周期）；
- `picks`：**系统探索推荐**（为迭代系统有效性，非主推）；优先个股（6 位代码），ETF 可作参考；
- `plans`：**必须覆盖全部关注/持仓股**（cards 表 + 候选池 core），给出具体触发/止损位；
- 推荐标的需带**触发条件**（回踩 X 日线/放量突破）与**止损位**（复盘反馈：明确触发+止损是预案可验证的前提）。

## 质量复盘（3 步）
1. **标的对照**：每个 plan 的 picks 股票，查其**预案次日**（created_at 后第一个交易日）daily_bars 涨跌幅；
2. **方向对照**：direction 判断 vs 当日板块涨幅（industry_bars）；
3. **LLM 总结**：`{quality 契合度, fixes 改进建议}` —— fixes 是**本 skill 的迭代输入**。

## 落库与读取
- 写入：盘后日报发送层 `_persist_plan(plan_data)`（pipeline/scheduler）；
- 读取：盘中报告点4 预案对照（B1 `_read_plan` 读最近 active plan）；
- 复盘：A3 `_plan_history` 取最近 5 天 plan + 次日实际。

## 迭代机制（沉淀为系统资产）
```
预案生成(5要素) → 落库 source='plan' → 次日实际 → 质量复盘(fixes)
     ↑                                                │
     └──────────────── fix 写入本 skill 方法论 ◄──────┘
```
- 每次复盘产生的 `fixes`（如"增加 ETF 量能权重""预案需明确触发条件与止损位"）
  应人工审核后回填本文件「迭代记录」，或更新 plan_gen 的 prompt 约束；
- 目标：推演方式从"LLM 自由发挥"收敛为"可复现、可验证、可迭代"的系统能力。

## 迭代记录
- 2026-08-22 初版：5 要素 + 3 步复盘建立。
- 复盘反馈（首日实测）："数据不足无法评估，需补充次日实际行情数据、预案需明确触发条件与止损位"
  → 已落实到 picks.plan / plans.action 必须含触发与止损。
