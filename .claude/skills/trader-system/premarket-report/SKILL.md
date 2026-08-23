---
name: premarket-report
description: 盘前报告方法论（a0_premarket，A1+A2 合并版）。8:40 推送：隔夜外围（含提前开盘的日韩股市）+ LLM 影响解读 + 涨停异动监控 + 消息汇总。当日 8:30 先完成采集/quant/Agent 关注方向落盘。
---

# 盘前报告（a0_premarket）

## 触发
- 交易日 8:30 `premarket` 任务：采集 + quant + Agent 关注方向（仅结论，落盘 `data/premarket_agent.txt`）；
- 8:40 `morning_brief` 任务：生成并推送合并盘前报告。

## 结构（10 节）
1. 标题 + 数据截至
2. **隔夜外围（表格）**：美股三指/A50/日经225/韩国KOSPI/原油/黄金/白银/USDCNY
   - 日韩数据源：东财 push2delay `100.N225` / `100.KS11`（提前开盘，8:40 已有当日行情）
   - 韩国拿不到 → 该行省略（best-effort）
3. **外围影响（LLM）**：外围方向 → 今日 A 股风格/板块传导，2-4 句
4. 市场温度 + 倾向
5. 仓位/评级（一行）
6. 市场风格（一句话，去掉指数强弱榜）
7. **今日关注（Agent，仅结论）**：8:30 run_research 输出，每条一行 ≤25 字，无依据/失效条件
8. **涨停异动监控（表格）**：停牌（akshare stop_em best-effort）+ 风险提示/异动监控/业绩雷/司法雷/黑天鹅（电报 + LLM 筛选）+ 精简风险提示
9. **消息汇总**：宏观（仅 LLM 判断有变化时）/个股/市场外（社会热点如电影破圈也算）+ 影响解读
10. （删除项：龙虎榜净买入、板块主线、指数强弱榜、末尾宏观/仓位——归盘后日报）

## 数据源
- 外围：新浪 `hq.sinajs.cn`（gb_/hf_ 系列）+ 腾讯汇率；日韩：东财 push2delay
- 停牌：akshare `stock_zh_a_stop_em`（网络不稳，重试 1 次失败省略）
- 消息素材：财联社电报 `stock_info_global_cls`（昨收 15:00 后 → 今晨）

## LLM（job='premarket'，2 次调用，缓存防重）
1. `overnight_analysis`：外围数据行 → 影响解读（失败省略）
2. `digest`：电报素材（截断 6000 字）→ JSON {risk_items, news{macro/stock/market_outside}, macro_changed, risk_summary}
   - JSON 解析失败 → 回退直列素材

## 落库
- Agent 关注方向：8:30 写 `data/premarket_agent.txt`（a0 读取，无文件省略）
- 无其他落库（盘前不产生可复盘观点）

## 失败回退
外围空 → 省略表格；LLM 失败 → 省略解读/直列素材；停牌失败 → 省略该列。报告永不阻断。
