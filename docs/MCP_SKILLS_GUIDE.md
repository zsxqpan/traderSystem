# 可用 MCP / Skill 精选清单与选型建议（2026-08-15）

> 面向 traderSystem（A 股投资决策辅助系统）评估。先明确一个关键前提：
> **本系统的 Agent 是 DeepSeek + 自研工具（invest/agent/tools.py），MCP 是给 Claude Code /
> Cursor 等客户端挂载的工具协议**。因此 MCP/Skill 的价值分两条路径：
>
> 1. **开发/研究期**：用 Claude Code 挂 MCP + Skill 辅助开发、查数、写分析（低风险，推荐）；
> 2. **运行期**：把 MCP server 的接口桥接进本系统 tools.py（让 DeepSeek Agent 多几个工具，工程量大，可选）。
>
> 另外注意：本系统**自采自算自推**（akshare/tushare 直连 + 自己的定量层），
> 纯行情类 MCP 与现有数据层高度重叠，价值在于「给 LLM 一条独立的查数通道」而非替代现有数据管道。

---

## 一、A 股行情/数据类 MCP（给 LLM 的查数通道）

| MCP | 链接 | 数据范围 | 与本系统结合点 | 建议 |
|---|---|---|---|---|
| **tushare-mcp-server** | [github.com/erwanjun/tushare-mcp-server](https://github.com/erwanjun/tushare-mcp-server) | A股/指数/基金/期货/期权/债券/港美股/宏观 | **本系统 TUSHARE_TOKEN 已配置**，可直接复用；给 Agent 加独立查数通道 | ⭐⭐⭐ 推荐 |
| **akshare-mcp** | [github.com/iefnaf/akshare-mcp](https://github.com/iefnaf/akshare-mcp) | AKShare 全量接口（uvx 安装，轻量） | 与系统现有 akshare 层同源，作为 Agent 查数备用 | ⭐⭐ 可选 |
| **china-stock-mcp** | [github.com/xinkuang/china-stock-mcp](https://github.com/xinkuang/china-stock-mcp) | 中国股市（akshare-one 封装） | 同上，纯行情，与现有层重复 | ⭐ 不推荐（重复） |
| **FinanceMCP** | [github.com/Xxx00xxX33/FinanceMCP](https://github.com/xxx00xxx33/financemcp) | Tushare + 财经新闻 + 国家统计局数据 | 新闻/宏观部分有增量价值 | ⭐⭐ 可选 |
| **aktools-pro** | [github.com/tchivs/aktools-pro](https://github.com/tchivs/aktools-pro) | A股/港美股/加密/贵金属/外汇/期货/基金/宏观 + 回测/模拟盘/ASCII图 | 功能最全但偏重；回测/模拟盘与系统重复 | ⭐ 备选（重） |
| **marketMcp** | [github.com/qiupo/marketMcp](https://github.com/qiupo/marketMcp) | 行情类 | 纯行情重复 | ⭐ 不推荐 |
| mseep-mcp-server-akshare | [pypi.org/project/mseep-mcp-server-akshare](https://pypi.org/project/mseep-mcp-server-akshare/0.1.0/) | AKShare | 同 akshare-mcp | ⭐ 备选 |

## 二、金融分析 Skills（分析方法论 / 提示词资产）

| Skill | 链接 | 内容 | 结合点 | 建议 |
|---|---|---|---|---|
| **A-Stock-Skills（29个）** | [github.com/ZICXR/A-Stock-Skills](https://github.com/ZICXR/A-Stock-Skills) | 数据采集/大盘分析/资金流向/涨停追踪/技术面/基本面/估值/财报/多因子/回测/风控/智能报告/自选股监控 | 与系统能力高度对应，可**提取其分析流程与提示词**改进本系统 Agent | ⭐⭐⭐ 推荐 |
| **anthropics/financial-services（官方）** | [github.com/anthropics/financial-services](https://github.com/anthropics/financial-services) | Anthropic 官方金融 Skills（合规/风控/分析流程） | 权威方法论，可参考其分析框架 | ⭐⭐⭐ 推荐（原版英文） |
| **claude-for-financial-services-cn（63个）** | [github.com/ctkqiang/claude-for-financial-services-cn](https://github.com/ctkqiang/claude-for-financial-services-cn) | 官方版深度适配 A股，63 个 Skills | 中文 + A股适配，分析/报告提示词资产 | ⭐⭐⭐ 推荐 |
| **finance-quant-skills** | [github.com/lzwme/finance-quant-skills](https://github.com/lzwme/finance-quant-skills) | A股量化交易技能（tdxquant 通达信 / miniqmt 等） | 与系统量化层重叠；若接 QMT 实盘可参考 | ⭐⭐ 可选（偏实盘） |

## 三、推送/通知类 MCP

| MCP | 链接 | 功能 | 结合点 | 建议 |
|---|---|---|---|---|
| **mcp-notify** | [github.com/aahl/mcp-notify](https://github.com/aahl/mcp-notify) | 微信/Telegram/Bark/**飞书**/钉钉多平台推送 | 系统已有企业微信+飞书+微信推送；若需钉钉/Telegram/Bark 通道或让 Agent 直接发消息可用 | ⭐⭐ 可选（补通道） |

## 四、通用/开发期 MCP（Claude Code 辅助）

| MCP | 链接 | 功能 | 结合点 | 建议 |
|---|---|---|---|---|
| **SQLite MCP**（官方） | [github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | 让 Claude Code 直接查 `data/invest.db` | 开发/复盘时直接查库分析，最实用 | ⭐⭐⭐ 推荐（开发期） |
| **Filesystem MCP**（官方） | [github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | 文件读写访问 | 开发期管理仓库文件 | ⭐⭐ 可选 |
| **Playwright MCP** | [github.com/microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) | 浏览器自动化/爬虫 | 数据源被拦时可让 Agent 走浏览器兜底 | ⭐⭐ 可选 |
| **Excel/图表 MCP**（yzfly/mcp-excel-server 等） | [github.com/yzfly/mcp-excel-server](https://github.com/yzfly/mcp-excel-server) | Excel 处理/可视化 | 若日报要做成 Excel/图文附件可用 | ⭐ 备选 |

---

## 三、选型建议（按你的需求）

### 场景 A：只想让 AI 更懂我的系统 / 辅助开发调试 → 推荐组合
1. **SQLite MCP**（官方）+ **Filesystem MCP**：Claude Code 直接查 `data/invest.db`，调代码、看数据、写复盘，零成本高收益。
2. **tushare-mcp-server**：token 已有，Claude Code 里直接查 Tushare 补充系统未采集的数据。
3. 配合 Claude Code 使用（本机已有 myenv + 全量数据，非常适合）。

### 场景 B：想提升系统内 Agent 的分析质量 → 推荐组合
1. **A-Stock-Skills**（29个）：拆它的分析流程/提示词，移植到本系统 `invest/agent/agents.py` 的 system prompt 与工具编排。
2. **claude-for-financial-services-cn**（63个）：取中文报告/财报/风控类 Skill 的提示词资产。
3. **anthropics/financial-services**：原版方法论对照。

### 场景 C：想让 Agent 运行期多几个外部工具（工程量大，可选）
把 MCP server 接口桥接进 `invest/agent/tools.py`（如 tushare-mcp 的查数、mcp-notify 的推送），
需实现 MCP 客户端调用（stdio/HTTP），约 1-2 天工作量。**建议先用场景 B 改善提示词，收益更大。**

### 不建议引入（与现有能力重复或偏离定位）
- 纯行情类 MCP（china-stock-mcp / marketMcp / akshare 系多款重复）：系统已自采自算。
- aktools-pro：含模拟盘/回测，与系统自己的回测/纪律层重复且引入额外依赖。
- finance-quant-skills：偏实盘接入（QMT/通达信），系统明确"不自动交易"。

---

## 四、接入方式速查（若选 Claude Code）

```bash
# Claude Code 安装 MCP（示例：SQLite + tushare）
claude mcp add sqlite -e npx -y @modelcontextprotocol/server-sqlite -- data/invest.db
claude mcp add tushare -e uvx tushare-mcp-server
# 查看
claude mcp list
```

Skills 安装：把 `A-Stock-Skills` / `claude-for-financial-services-cn` 克隆后，
Skills 目录（`~/.claude/skills` 或项目 `.claude/skills`）放入对应文件夹即可被 Claude Code 自动发现。
