# 互联网大神投研 Skill 精选清单（2026-08-16）

> 聚焦"白毛股神 Serenity"等互联网知名交易者/大V 的方法论 Skill，供选择。
> 标注：⭐=推荐 / ✅=有验证或已装 / ⚠️=宣称数据未独立核实

## 一、白毛股神 Serenity 专属 Skill（4 个版本）

| Skill | 链接 | 说明 | 建议 |
|---|---|---|---|
| **serenity-skill（蒸馏版）** | [github.com/yijiashu/serenity-skill](https://github.com/yijiashu/serenity-skill) | **蒸馏白毛股神 5000+ 推文**得到的 skill——最贴合本人方法论 | ⭐⭐⭐ 首选 |
| **serenity-skill（方法论版）** | [github.com/ZadAnthony/serenity-skill](https://github.com/ZadAnthony/serenity-skill) | 供应链卡点逆向投资方法论（第三方提炼） | ⭐⭐⭐ 推荐 |
| **Serenity-Skill（量化版）** | [github.com/perplegg/Serenity-Skill](https://github.com/perplegg/Serenity-Skill) | 方法论定量化实现 | ⭐⭐ 备选 |
| **BestSerenitySkillFromAT** | [github.com/yux1azhengye/BestSerenitySkillFromAT](https://github.com/yux1azhengye/BestSerenitySkillFromAT) | 整合版（AlphaTerminal） | ⭐⭐ 备选 |

**白毛股神方法论核心**（供参考）：
- **3612% 收益率神话**（5个月500% / 两年225倍，⚠️ 自报未独立核实）
- **卡脖子投资法 / 紫苏叶理论**：寻找供应链关键卡点（"紫苏叶"= 卡脖子环节）→ 押注国产替代
- 逆向、聚焦稀缺卡位公司

## 二、游资/龙头战法 Skill

| Skill | 链接 | 说明 | 建议 |
|---|---|---|---|
| **UZI-Skill（66位大佬合集）** | [github.com/wbh604/UZI-Skill](https://github.com/wbh604/UZI-Skill) | **66 位投资大佬**看盘：22 维数据 × 180 条量化规则 × 17 种机构分析，A股/港股/美股 | ⭐⭐⭐ 推荐（覆盖面最广） |
| **youzi-trading-skill** | [github.com/AIPMAndy/youzi-trading-skill](https://github.com/AIPMAndy/youzi-trading-skill) | 23 位著名游资心法（情绪周期/龙头战法/概率思维）——✅ 已装到 Hermes | ✅ 已装 |

## 三、AI 投研框架（Codex/Claude 可用）

| Skill | 链接 | 说明 | 建议 |
|---|---|---|---|
| **stock-analysis-skill** | [github.com/tigersking520/stock-analysis-skill](https://github.com/tigersking520/stock-analysis-skill) | 面向 Codex/Claude Code 的专业个股投研 Skill（产业链/财报/情绪/估值/反方报告） | ⭐⭐⭐ 推荐（与你 Codex 环境直接匹配） |
| **ai-berkshire** | [github.com/xbtlin/ai-berkshire](https://github.com/xbtlin/ai-berkshire) | 巴菲特风格 AI 投研（价值投资框架） | ⭐⭐ 可选 |
| **aiagents-stock** | [github.com/oficcejo/aiagents-stock](https://github.com/oficcejo/aiagents-stock) | 复合多 AI 智能体股票团队（龙虎榜/板块预警/实时监测，预留 miniqmt） | ⭐⭐ 备选（功能重） |
| **TradingAgents-astock** | [github.com/simonlin1212/TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock) | A股多 Agent 投研（7 分析师辩论，龙虎榜/游资/解禁适配） | ⭐⭐⭐ 架构参考 |

## 四、其他大V / 方法论 Skill（搜索中发现）

| Skill | 链接 | 说明 |
|---|---|---|
| **chen-xiaoqun-skill** | [github.com/sherjy/chen-xiaoqun-skill](https://github.com/sherjy/chen-xiaoqun-skill) | 陈小群（知名游资）方法论 skill |
| **haoyunge（浩云哥）skill** | 见 OpenClaw 社区 | 浩云哥视角 skill |
| **lhb-analyzer** | 见 tool.lu | 龙虎榜分析 skill |

---

## 建议（按性价比）

1. **serenity-skill 蒸馏版**（yijiashu）→ 白毛股神 5000 推文方法论，最贴合需求
2. **UZI-Skill**（66 位大佬）→ 覆盖面最广的游资合集
3. **stock-analysis-skill** → 直接匹配你的 Codex 环境

> ⚠️ 提醒：白毛股神"3612% 收益率"为自报战绩，未独立核实；Skill 价值在于**方法论结构化**（供应链卡点逆向思维），不是照搬收益。所有 Skill 只做决策辅助，不自动交易。

## 安装状态（2026-08-16）

✅ 三个 skill 已克隆到 `tools/hermes_skills/`：
| Skill | 位置 | 内容 |
|---|---|---|
| serenity-skill | `tools/hermes_skills/serenity-skill/` | **44,514 条推文**蒸馏的机构级投资思维（SKILL.md + scripts） |
| UZI-skill | `tools/hermes_skills/UZI-skill/` | 66 位大佬，447 文件，含 4 子 skill（deep-analysis/investor-panel/lhb-analyzer/trap-detector）+ serenity 档案 |
| stock-analysis-skill | `tools/hermes_skills/stock-analysis-skill/` | A股/港股/美股五步法投研（财务排雷/市值倒推/反证清单） |

### 安装到 Hermes（需手动复制，E 盘沙箱不可写）
```powershell
Copy-Item -Recurse "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem\tools\hermes_skills\serenity-skill" "E:\Hermes Agent CN Desktop\data\hermes-home\skills\finance\serenity-skill"
Copy-Item -Recurse "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem\tools\hermes_skills\stock-analysis-skill" "E:\Hermes Agent CN Desktop\data\hermes-home\skills\finance\stock-analysis-skill"
Copy-Item -Recurse "C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem\tools\hermes_skills\UZI-skill" "E:\Hermes Agent CN Desktop\data\hermes-home\skills\finance\UZI-skill"
```
重启 Hermes 后验证：问"用 Serenity 视角分析一下 XX" / "用 UZI 深度分析 XX" / "做个股五步法报告"。<br>
UZI-skill 也带 `.codex` 插件目录，可直接用于 Codex（放入 `~/.codex` 或按仓库 README）。
