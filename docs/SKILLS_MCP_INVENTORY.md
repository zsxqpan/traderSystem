# 系统已装 Skill 与 MCP 全量盘点（2026-08-16）

> 分四层：① 系统自身能力（invest/ 模块，非外部 skill）；② 项目内 skill 仓库；
> ③ Hermes 已装 skill（E 盘）；④ MCP（Codex 已启用 + 源码备用）。
> 每项标注【对应系统功能】。
>
> **2026-08-16 决策**：项目主要开发环境为 **DeepSeek Harness**，不再向 Hermes 复制
> skill（`tools/hermes_skills/` 下已克隆的 serenity/UZI/stock-analysis 保留在项目内，
> 作为方法论参考与提示词资产，直接供 Harness 中的 Agent 使用）。
>
> **2026-08-16 报告改版**：日报/盘中实时报告 = 短线操作辅助（异常波动做T/建仓时机/盘面变化），
> 周报 = 中期配置（中线强度/估值分位/宏观）。详见 invest/report.py 与 test_report_short.py。

---

## 一、系统自身能力（invest/ 模块 — 已内置，非外部 skill）

| 模块 | 功能 | 对应外部 skill/MCP |
|---|---|---|
| invest/quant/* | 定量层：强度/轮动/温度/资金/联动/估值/拥挤度/宏观流动性/**Alpha158 因子/情绪周期** | 对应 A-Stock-Skills 的 astock-* |
| invest/discipline/* | 纪律层：对象池/价差/卡片/仓位/计划/风控/组合/凯利/**kill-gate** | 对应 UZI-skill 的 trap-detector |
| invest/agent/* | 双 Agent（投研/交易）+ 工单 + 仲裁 + cross_validate | 对应 TradingAgents / aiagents-stock |
| invest/review/* | 复盘：周/月/年 + 归因 + 错误分类 + BCS/VMS | 对应 astock-trade-review |
| invest/report.py | 报告：盘后日报/盘前/盘中实时（含情绪周期） | 对应 astock-report |
| invest/data/* | 数据：三源实时/行业/估值/PIT/主题/历史快照 | 对应 akshare 系 + akshare-tools MCP |
| invest/push/* | 推送：企业微信/飞书/个人微信 | 对应 mcp-notify / messaging-push-channels |

---

## 二、项目内 skill（`.claude/skills/` 4 仓库 + 展开 6 技能；`tools/hermes_skills/` 4 个）

### 2.1 `.claude/skills/`（Claude Code 格式，供 Codex/Claude 参考）

| Skill | 内容 | 对应系统功能 |
|---|---|---|
| **A-Stock-Skills**（仓库） | A 股分析全家桶 | 与 invest/ 高度重叠，方法论参考 |
| ├ 00-start-here | 上手指南 | — |
| ├ 01-infra | 数据源/缓存/工具（astock-data-source/cache/utils） | invest/data 数据层 |
| ├ 02-data-collection | trade-journal（AI建议vs实盘）/ watchlist-monitor | invest/review + intraday |
| ├ 04-stock-analysis | 技术面分析（均线/MACD/KDJ/RSI/支撑压力） | invest/quant 补充 |
| ├ 05-quant | screener 筛选器 | invest/discipline 对象池 |
| ├ 05-reports | 日报/个股研报/持仓报告 | invest/report.py |
| └ 06-tools | alerter 告警推送 | invest/push |
| **anthropic-financial-services** | Anthropic 官方金融 skill（合规/风控/分析） | invest/review 方法论参考 |
| **claude-for-financial-services-cn** | 中文 A股 金融（63 skill） | 同上，中文资产 |
| **finance-quant-skills** | A股量化 13 子技能（akshare/tushare/backtrader/miniqmt 等） | invest/quant + backtest |

### 2.2 `tools/hermes_skills/`（Hermes 专用，4 个）

| Skill | 内容 | 对应系统功能 | Hermes 安装状态 |
|---|---|---|---|
| **youzi-trading** | 23 位游资心法（情绪周期/龙头战法/概率思维） | 情绪周期 ↔ invest/quant/emotion_cycle.py | ✅ 已复制 |
| **serenity-skill** | 白毛股神 44,514 推文蒸馏（产业链卡点/机构视角/估值） | 产业链分析 ↔ invest/discipline/spread.py | ⏳ 未复制 |
| **UZI-skill** | 66 位大佬（deep-analysis/investor-panel/lhb-analyzer/trap-detector） | 龙虎榜↔invest/data 龙虎榜；陷阱↔kill-gate | ⏳ 未复制 |
| **stock-analysis-skill** | 五步法投研（财务排雷/市值倒推/反证清单） | 个股分析 ↔ invest/discipline/cards.py | ⏳ 未复制 |

---

## 三、Hermes 已装 skill（E 盘 `skills/finance/`，29 个）

| 系列 | Skill | 对应系统功能 |
|---|---|---|
| **astock-*（13）** | data-source / cache / utils / alerter / report / screener / technical-analysis / trade-journal / trade-review / watchlist-monitor / start-here / a-share-market-data / a-share-multi-index-style | invest/data + quant + review + intraday |
| **quant-*（13）** | akshare / akquant / backtrader / baostock / equity-researcher / joinquant-docs / jqdatasdk / miniqmt / pywencai / qmt-docs / rqalpha / tdxquant / tushare | invest/quant 数据与回测 |
| **其他** | china-a-share-data / messaging-push-channels | invest/data + invest/push |
| **游资** | youzi-trading ✅ | invest/quant/emotion_cycle.py |
| 另有 research 分类 | arxiv / blogwatcher / cn-market-data / llm-wiki / polymarket / research-paper-writing | invest/agent 研究辅助 |

---

## 四、MCP（Codex 已启用 3 + 源码备用 3 + 系统自研 1）

| MCP | 状态 | 配置 | 对应系统功能 |
|---|---|---|---|
| **sqlite-invest**（自研） | ✅ Codex 已启用 | config.toml → tools/mcp/local_sqlite_mcp.py | 只读查 invest.db（全部表） |
| **akshare-tools** | ✅ Codex 已启用 | config.toml → uv akshare-tools | akshare 数据接口 ↔ invest/data |
| **mcp-notify** | ✅ Codex 已启用 | config.toml → uv mcp-notify | 推送 ↔ invest/push |
| node_repl | ✅ Codex 内置 | config.toml | JS 运行（开发用） |
| akshare-mcp（源码） | ⏳ 备用 | tools/mcp/akshare-mcp/ | akshare 接口（与 akshare-tools 重复） |
| FinanceMCP（源码） | ⏳ 备用 | tools/mcp/FinanceMCP/ | Tushare+新闻+统计局 |
| mcp-excel-server（源码） | ⏳ 备用 | tools/mcp/mcp-excel-server/ | Excel/图表导出 |

---

## 五、总结：功能覆盖矩阵

| 系统功能 | 内部实现 | 外部 skill | MCP |
|---|---|---|---|
| 数据采集 | invest/data | astock-data-source / quant-akshare | akshare-tools / sqlite-invest |
| 定量计算 | invest/quant | astock-technical-analysis / quant-backtrader | — |
| 选股入池 | invest/discipline | astock-screener / UZI deep-analysis | — |
| 个股分析 | cards.py | stock-analysis-skill / serenity-skill | — |
| 情绪周期 | emotion_cycle.py | youzi-trading / UZI lhb-analyzer | — |
| 风控纪律 | discipline/* + kill-gate | astock-trade-review / UZI trap-detector | — |
| 复盘 | review/* | astock-trade-journal / anthropic 金融 | — |
| 推送 | push/* | astock-alerter / messaging-push | mcp-notify |
| 报告 | report.py | astock-report / stock-analysis | — |
