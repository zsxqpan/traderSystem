---
name: intraday-report
description: 盘中实时报告方法论（b1_intraday）。@机器人/私聊触发，默认完整版，说"简洁/简短"发简洁版（只留客观盘面）。4 点结构：盘面总览（含 ETF 大资金信号）/情绪预测/日内主线（ETF+推荐股）/核心关注与预案对照。
---

# 盘中实时报告（b1_intraday）

## 触发
- 飞书群 @ 机器人 / 私聊 / 管理员不@语义触发（意图识别：群聊关键词优先+LLM 兜底；私聊纯 LLM）
- 限频：120s/人（报告含 2 次 LLM 调用）
- 版本：默认**完整版**；消息含"简洁/简短/精简/简版"→ 简洁版（去掉所有推演/观点/预案，只留客观盘面）

## 结构（4 点）
1. **盘面总览**：8 大指数实时表格（腾讯 `qt.gtimg.cn`，涨跌额算涨跌幅）+ 大小盘结构分化
   + 指数涨跌幅条形图（matplotlib PNG → 飞书 image）+ **指数 ETF 大资金信号**
   （量比≥2 / 超大单±10亿 ≈ 国家队/大资金动作）
2. **情绪判断 + 盘面预测 + 短线博弈（LLM，youzi 方法论）**：
   - 输入：温度 + 情绪周期（emotion_cycle）+ 盘中连板（涨停数/最高板/炸板率，limit_up_pool 5 分钟落库）+ 近 20 日温度
   - 输出：{mood, prediction, short_term}（滞涨回落/放量下跌不抄底/主升日/龙头滞涨杂毛补涨即将分歧等）
3. **日内主线（LLM）**：最强方向 → 原因 / 内部结构（大小盘、细分分化）/**板块 ETF 强度**/**推荐关注股票**/龙头（连板·趋势·容量·行业）/走势推演（一日游/反弹/分化/缩圈/扩圈/分歧）
4. **核心关注与预案对照（点4+5 合并）**：核心关注行情表格 + 所属板块补充分析（若点3 未覆盖）
   + 走势推演 + 盘后预案对照（viewpoints source='plan'）

## 数据源
- 指数：腾讯指数快照（`[3]=现价 [4]=涨跌额`，涨跌幅=涨跌额/(现价-涨跌额)）
- ETF：akshare `fund_etf_spot_em`（成交额/换手/量比/主力净流入/超大单，一次拉全市场过滤）
- 连板/资金：limit_up_pool / sector_fund_flow（盘中 5 分钟落库）

## LLM（job='intraday_report'，2 次调用，120s 缓存防抖）
1. `mood_llm`：情绪 + 预测 + 短线博弈（youzi 方法论，max_tokens 600）
2. `mainline_llm`：主线分析（输入含板块 ETF，输出加 etf/picks 字段，max_tokens 2000）
- 失败 → 规则回退：情绪用 emotion_cycle stage/reasons/guide；主线直列板块/资金/连板/ETF

## 落库（发送层完成，skill 保持纯函数）
- 观点 → `viewpoints source='intraday_report'`（obj=mood/mainline，conclusion=JSON），
  盘后日报点2 复盘读取

## 飞书版面
- interactive 卡片：table 组件（表格）+ image 组件（matplotlib PNG）+ lark_md **加粗**（*星号*自动转）
- 企微/微信：render_plain 纯文本（表格转紧凑行、图表转数据行）
- 图表上传失败/卡片失败 → 自动降级文本，不丢消息
