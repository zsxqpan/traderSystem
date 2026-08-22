# 分析类 Skill 使用指南（2026-08-21）

本项目有 4 个**金融分析** skill（位于 `tools/hermes_skills/` 与 `.claude/skills/`），
它们不是"写代码"技能，而是**分析方法论**——供 LLM/Agent 做股票分析时参考。
任何代码 Agent 在回答**投资/分析类问题**时，按问题性质选用对应方法论：

| Skill | 方法论摘要 | 适用问题 |
|---|---|---|
| **Serenity**（serenity-skill，44,514 条推文蒸馏） | 机构级投资思维：护城河 / 景气度 / 估值分位 / 逆向思考 | 产业链研究、行业基本面、中长期方向 |
| **youzi-trading**（23 位游资心法） | 情绪周期 / 龙头战法 / 概率思维 / 仓位管理 | 短线走势、连板与异动股的操作建议 |
| **stock-analysis**（五步法投研） | 财务排雷 / 市值倒推 / 反证清单 | 个股中长线基本面研判 |
| **UZI-Skill**（deep-analysis 等 4 子技能） | 深度个股分析流水线（数据→多维→评分→报告） | 个股深度分析（重） |

## 已接入的通道
1. **飞书会话 Agent**（`invest/agent/agents.py` 的 `CHAT_SYSTEM`）：已内置
   serenity / youzi / stock_analysis 三个方法论摘要，模型按语义自选并在回复末尾
   自标注「↘ 已使用 Skill：xxx」；
2. **本项目文档**：本文件供任何代码 Agent 在分析类任务里参考。

## 说明
- DSH（DeepSeek Harness）的会话 skill 目录由运行时管理，项目侧无法直接注册；
  如需在 DSH 侧挂载，可在 DSH 的技能管理界面把上述 SKILL.md 添加为会话技能，
  或在提问时注明"用 Serenity 视角 / 用游资视角"由模型自行套用。
