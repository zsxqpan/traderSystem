# 知名交易者/高星开源量化项目精选清单

> 2026-08-16 整理 | 面向 traderSystem（A 股决策辅助系统）评估
> 原则：优先 star 高、有回测/实盘收益率验证、A 股相关、可借鉴/可集成者。
> 标注 ⚠️ = 收益率"宣称"未经独立核实；✅ = 有公开验证数据。
> **重要提醒**：除标注 ✅ 项外，绝大多数高星策略的 win rate/年化均为自报或不可复现，**不要照搬任何"宣称收益"**；最有价值的是其验证方法与因子/架构资产。

---

## 一、A 股高星量化框架（可借鉴架构/因子库）

| 项目 | Star | 功能 | 对本系统价值 |
|---|---|---|---|
| [vnpy/vnpy](https://github.com/vnpy/vnpy) | ~4.4万 | 国内最成熟实盘框架（CTA/套利/期权+CTP 网关+风控） | 架构参考（行情/交易/风控分层），实盘网关暂不必集成 |
| [microsoft/qlib](https://github.com/microsoft/qlib) | ~3.7万 | 微软 AI 量化：Alpha158/360 因子库、ML/DL/RL、回测、RD-Agent | **最高性价比**：因子库+模型+回测一体化，论文级 benchmark（IC/年化/Sharpe 公开可复现） |
| [mementum/backtrader](https://github.com/mementum/backtrader) | ~1.7万 | 通用事件驱动回测，指标/分析器/优化器 | 轻量可嵌入，配 akshare 快速验证策略 |
| [akfamily/akshare](https://github.com/akfamily/akshare) | ~2.1万 | 免费 A 股全量数据接口 | **数据层首选**（本系统已在用） |
| [UFund-Me/Qbot](https://github.com/UFund-Me/Qbot) | ~7k | 数据-因子-策略-回测-实盘一体化 | 二次开发脚手架 |
| [ricequant/rqalpha](https://github.com/ricequant/rqalpha) | ~6k | 米筐开源，A 股语义最贴合（涨跌停/除权处理完善） | 策略语义参考 |
| [waditu/tushare](https://github.com/waditu/tushare) | ~5k | 老牌 A 股接口（部分需积分） | akshare 补充源（已接入） |
| [shidenggui/easytrader](https://github.com/shidenggui/easytrader) | ~6k | 券商客户端自动下单 | 若未来自动交易用（当前系统不自动交易） |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | ~1.5万 | 强化学习交易框架（NeurIPS 论文+FinRL Contest 基准） | RL 交易环境建模与统一评估协议 |
| [wondertrader/wondertrader](https://github.com/wondertrader/wondertrader) | ~2k | C++ 全品种研发交易框架 | 仅借鉴分层架构 |
| [wilsonfreitas/awesome-quant](https://github.com/wilsonfreitas/awesome-quant) | ~1.8万 | 全球量化资源索引 | 找轮子入口 |
| [thuquant/awesome-quant](https://github.com/thuquant/awesome-quant) | ~2.5k | 清华系中国 Quant 资源索引 | 中文资料检索 |

## 二、A 股专项策略（多因子/轮动/情绪，贴合本系统）

| 项目 | 验证 | 功能 |
|---|---|---|
| [etf-rotation-strategy](https://github.com/zhangsensen/etf-rotation-strategy) | ✅ 实盘胜率 83.3% | 三层验证引擎（WFO→VEC→BT）生产级 ETF 轮动 |
| [aurumq-rl](https://github.com/yupoet/aurumq-rl) | 未披露 | 296 因子（Alpha101+Alpha191）、涨跌停感知、A股 RL 选股 |
| [quantdash-ai-stock](https://github.com/rancy777/quantdash-ai-stock) | 未披露 | **与本系统同构**：情绪周期+板块轮动+AI复盘+MCP+飞书 |
| [A-Stock-Skills](https://github.com/ZICXR/A-Stock-Skills) | 未披露 | 29 个 A 股分析 Skills（已装） |
| [youzi-trading-skill](https://github.com/AIPMAndy/youzi-trading-skill) | 未披露 | **23 位著名游资心法**超短线 Skill，适配 Hermes |
| [BAISYS_QUANT](https://github.com/baisysquant/BAISYS_QUANT) | 未披露 | 6 门控 51 规则 + MACD 7 维管线复盘 |
| [WAYLON/ashare-quant-strategies](https://github.com/WAYLON/ashare-quant-strategies) | 学习向 | 119 篇 A 股量化策略研究语料 |

## 三、AI 多 Agent 交易框架（与系统双 Agent 最相关）

| 项目 | 验证 | 说明 |
|---|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | ⚠️ 论文宣称累计 23.21% / 年化 24.90%（arXiv 2412.20138，~60K star） | 多智能体 LLM 交易团队（分析师/研究员/交易员/风控），框架参考价值高，收益数据未经独立复现 |
| [TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock) | 未披露 | **A 股改造版**：7 位分析师辩论，适配龙虎榜/游资/解禁 |

## 四、海外知名策略与验证体系（freqtrade 生态 / zipline / 论文因子）

### 4.1 Freqtrade 生态（验证最透明）

| 项目 | Star | 说明 | 可借鉴点 |
|---|---|---|---|
| [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | ~3.7万 | 通用加密量化框架 | ✅ 回测报告体系（胜率/盈亏比/最大回撤/持仓时长）+ hyperopt 寻优流程可移植到 A 股 |
| [iterativv/NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity) | ~3k | freqtrade 最著名策略 | **把回测断言写进 CI**（胜率/回撤自动门禁）——系统化验证做法值得照搬 |
| [freqtrade/freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies) | ~1.2k | 官方策略合集 | 策略模板分层（信号→入场→出场→风控）是模块化范本 |
| [froggleston/cryptofrog-strategies](https://github.com/froggleston/cryptofrog-strategies) | ~2.5k | 知名作者 Robert Davey | 策略"家族"迭代可追溯——适合 A 股策略版本管理 |
| [stash86/MultiMA_TSL](https://github.com/stash86/MultiMA_TSL) | ~200+ | 多均线趋势+移动止损 | 多周期确认+TSL 思路 |
| [francisx1999/crypto-trading-bot-postmortem](https://github.com/francisx1999/crypto-trading-bot-postmortem) | 数百 | **最诚实反例**：24 个月真实数据、7 策略全失败、代码单据全公开 | **"击杀门禁"（最大回撤/连亏/盈亏比硬门槛）——A股策略上线前防过拟合的最佳流程** |
| [thinkong/awesomefreqtrade](https://github.com/thinkong/awesomefreqtrade) | ~1k | 生态索引 | 资源发现 |
| FreqST（[freqst.com/charts](http://www.freqst.com/charts)） | — | 聚合社区真实回测 | 跨策略横向对比方法论——A股可建统一回测基准库 |

### 4.2 Zipline / Quantopian 遗留（经典但停维护）

| 项目 | Star | 说明 |
|---|---|---|
| [quantopian/zipline](https://github.com/quantopian/zipline) | ~1.7万 | 事件驱动回测引擎（已归档），架构参考 |
| [quantopian/pyfolio](https://github.com/quantopian/pyfolio) | ~5.5k | **专业绩效归因 tearsheet**（年化/Sharpe/回撤/月度热力图/因子暴露）——A股绩效评估模块可直接借鉴 |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | ~1万 | Quantopian 商业继任，多市场事件驱动 |

### 4.3 知名作者 / 论文因子

| 项目 | Star | 说明 |
|---|---|---|
| [Ernie Chan（陈韵）代码合集](https://github.com/dterg/quant_at) | ~1k | 《Quantitative Trading》书内策略，**配对交易/均值回归研究管线（协整→样本内外→样本外）最值得照搬** |
| [WorldQuant 101 Alphas 实现](https://github.com/ram-ki/101_formulaic_alphas) | ~1k | 101 式量价因子库（Kakushadze 2016 论文），现成因子源 |
| [huseinzol05/Stock-Prediction-Models](https://github.com/huseinzol05/Stock-Prediction-Models) | ~1万 | ML/DL 预测模型大全，评估协议清晰 |
| [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | ~8.8k | 轻量回测，快速原型验证 |
| Jim Simons / Medallion | — | ⚠️ **年化 66% 历史战绩从未开源**，只能借鉴公开理念（数据驱动/统计套利），无代码可学 |

---

## TOP 推荐（按验证可信度 + 对 A 股可借鉴性）

| 排名 | 项目 | 推荐理由 |
|---|---|---|
| **1** | [microsoft/qlib](https://github.com/microsoft/qlib) | 论文级 benchmark 公开可复现，Alpha158/360 因子库+A 股数据脚本，是本系统"因子研究+模型评估"最佳底座 |
| **2** | [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) + [NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity) | 验证最透明：回测报告体系 + CI 断言门禁 + hyperopt，策略工厂改造模板 |
| **3** | [francisx1999/crypto-trading-bot-postmortem](https://github.com/francisx1999/crypto-trading-bot-postmortem) | 最诚实的反例，kill-gate 防过拟合流程是本系统上线门禁的最佳参考 |
| **4** | [quantopian/pyfolio](https://github.com/quantopian/pyfolio) | 专业绩效归因 tearsheet，直接升级本系统复盘指标 |
| **5** | [TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock) | A 股多 Agent 辩论框架（龙虎榜/游资/解禁适配），与系统双 Agent 架构互补 |

## 关键结论

1. **收益数据可信度极低**：除标注 ✅ 项外，几乎所有高星策略的收益均为自报/不可复现，不要照搬。
2. **最值得借鉴三类资产**：① qlib 因子库与模型 benchmark；② freqtrade 回测指标与 CI 验证体系；③ pyfolio 绩效归因。
3. **防过拟合第一优先级**：postmortem 式 kill-gate 验证流程应作为本系统策略上线门禁。
4. **与本系统同构项目**（quantdash-ai-stock、youzi-trading-skill）优先对照，借鉴其已趟过的坑。
