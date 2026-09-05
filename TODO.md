## 📋 完成度与测试覆盖追踪

### 2026-08-28 可靠性与证据驾驶舱
- 介绍与用法已重写：`docs/SYSTEM_GUIDE.md`（飞书 / 中期比价 / 报告 / 任务）、`docs/OPERATIONS.md`（补偿 / 账本 / 排障）。
- 部署默认 **ticker-only**；OS 计划任务 **13** 个（与 `JOB_FUNCS` / `install_os_tasks.ps1` 校验一致），盘中轮询 **10 秒**。
- 晚间只发 **22:00 evening_report** 一份（已合并原 16:00 日报 / 21:35 P2 简报 / 22:00 复盘）；16:00 after_close 不再推送日报。
- 仪表盘 **9** 页（含中期比价）；空 as_of 落到最新已落库事实卡时点。
- 行为评测：`tests/test_eval_e2e.py`（工具计划 / 数字可追溯 / 实时覆盖 / 报告送达 / 静默失败）。
- 盘中 10s ticker 真实交易时段仍未在周末实测；阶段 1 季度闭环 / BCS 季度核验仍属 [C]。

### 总体进度
- 本隔离工作树（`reliability-evidence-cockpit`）做任务 1–6 可靠性/证据驾驶舱改造；完成度以本工作树改造项与 `pytest` 为准，不再沿用 2026-08-15 盘点的精确勾选计数（曾与 `SYSTEM_GUIDE` 互相打架）。
- [A] 类「代码可做 12 项」已于 2026-08-15 全部落地（含代码侧完成、数据源仍待接入的项）
- pytest 全量：以本机 `pytest tests/` 为准（不再沿用 2026-08-15 的 153 passed 过时计数）
- 真实数据闭环 E2E（scripts/e2e_phase1.py）：对象池→因子→卡片→风控→计划 ✅

### 已完成且有单元测试的模块（61 项中的主体）
| 模块 | 测试文件 | 状态 |
|---|---|---|
| 实时行情三源直连/新鲜度/留痕 | test_realtime.py (8) | ✅ |
| 盘中异动监测/推送分级 | test_intraday.py (5) | ✅ |
| P0 监控（证伪/风控/数据冲突） | test_monitor.py (3) | ✅ |
| 收盘扫描快照/P1 推送 | test_scan.py (3) | ✅ |
| 风控规则/数据失效降级 | test_discipline.py (7) | ✅ |
| 组合风险簇/预算 | test_clusters.py (4) | ✅ |
| 执行成本/可交易性/冻结 | test_costs.py (5) | ✅ |
| 数据 PIT 化/四状态/决策留存 | test_pit.py (5) | ✅ |
| 共线性/拥挤度状态机 | test_factors.py (6) | ✅ |
| 权重治理/版本管理/OOS | test_governance.py (5) | ✅ |
| 凯利/Wilson/格子决策 | test_kelly.py (5) | ✅ |
| 回撤限额/压力测试 | test_limits.py (3) | ✅ |
| BCS/VMS/一票否决 | test_bcs.py (4) | ✅ |
| 因子有效性 IC/ICIR/分组 | test_factor_eval.py (5) | ✅ |
| 归因/错误分类/年度复盘 | test_review2.py (5) + test_review.py (3) | ✅ |
| 对象池硬门槛/冻结名单 | test_cards.py (7) 内 | ✅ |
| 机会卡片/状态机/赔率 | test_cards.py (7) | ✅ |
| 主价差/因子打分/参照物 | test_spread.py (6) | ✅ |
| 宏观评级/总闸/黑天鹅 | test_macro_position.py (7) 内 | ✅ |
| 固定风险 R/计划模板 | test_macro_position.py (7) 内 | ✅ |
| 量化层（strength/rotation/temperature/capital/linkage/valuation/weekly） | test_quant.py (16) | ✅ |
| 回测引擎/趋势阶段 | test_backtest.py (9) | ✅ |
| [A] 类新功能（行业映射/L3主题/结构断点/发现器/周期漂移/复盘v1/P2简报/快照重建/历史快照/因子自动化/PB分位） | test_todo_a.py (13) | ✅ |

### 已勾选但无测试覆盖或测试被排除的项（诚实标注）
| 项 | 实际状态 |
|---|---|
| 仪表盘 Streamlit **9** 页面（含中期比价；2026-08-04 初版为 6 页） | test_dashboard.py 存在但未纳入全量 pytest（依赖窗口环境，未验证） |
| 企业微信推送 / 飞书通道 | test_pipeline.py 覆盖 mock 逻辑；**真实推送未做端到端验证**（需 webhook） |
| 龙虎榜/两融/宏观采集（2026-08-04） | 真实库已验证入库，但 test_data.py (28) 被排除（依赖真实网络/慢） |
| 收盘扫描 P1 推送（scan.py） | 快照逻辑有测试；**推送发送链路未真实触发**（无变化时静默） |
| scheduler 例行任务 | test_pipeline / test_reliable_jobs 断言 13 个 JOB_FUNCS + OS 清单；**盘中 10 秒 ticker 未在真实交易时段跑过** |
| 行业估值采集（2026-08-15 修复） | 真实采集 293 行入库验证；无独立单测（依赖网络） |

### ⬜ 仍开放项（历史分类，数量以当前清单为准）

**[A] 代码可做（12 项）— 已于 2026-08-15 全部完成**
1. ✅ 行业 PE/PB 估值分位：代码就绪（pb 列 + compute_pb_percentile + pipeline 合并）；数据源接入仍属 [B]
2. ✅ 个股→行业映射持久化（data/industry_stocks.json 手工映射兜底 + industry_map.py）
3. ✅ L3 主题/产业链清单（data/themes.json 首批 12 个 + themes.py）
4. ✅ 结构断点检查（spread.py 已知断点 + 统计检测，截断历史窗口防假极值）
5. ✅ 榜单降级为「发现器」（mispricing_necessary + check_and_add require_mispricing）
6. ✅ 执行留痕：计划/成交偏差 + 周期漂移检测（records.py detect_cycle_drift）
7. ✅ 复盘 v1：周度纪律+持仓卡片复评；月度环境质量检查
8. ✅ hermes-agent P2 例行简报（历史 21:35）→ **2026-08-18 已并入 22:00 evening_report，不再单独调度**
9. ✅ 快照重建（scan.py rebuild_snapshot/rebuild_pool/rebuild_ratings/rebuild_quant）
10. ✅ 历史行业归属/成分/ST 状态按历史时点保存（v1：universe.py 每日快照 + 回溯；全量回填待评估）
11. ✅ 因子与价差计算自动化（auto.py 四套周期镜像全标的自动打分）
12. ✅ 四套周期镜像全部启用（CYCLE_MIRRORS 波段/配置/事件博弈/趋势）

**[B] 需用户执行（8 项）**
1. ✅ Tushare token 配置（2026-08-15 已验证，rows=149）→ 备用源 + 日线交叉校验
2. ✅ 社融增量数据源接入（2026-08-15：商务部源 macro_china_shrzgm 接入，quant_macro 输出社融增量）
3. ✅ 行业全量首跑验证（2026-08-15 用户本机 collect：industry_all=57060 行，行业数 90）
4. ✅ 龙虎榜席位类型（2026-08-15：链路打通 + 修复 2 个 bug；真实样本待候选池扩充）
5. ✅ 数据回填执行（2026-08-15 已完成：个股/指数/行业最早均到 2020-01-02）
6. ✅ 本机跑 pytest 全量（2026-08-15：EXITCODE=0 全部通过）
7. FastAPI 接口层（按需；代码已就绪，需要时 `scripts/run_api.py`）
8. ✅ 环境重评触发条件落地（2026-08-15：ERP 跨分位/社融拐点/10Y>20bp 三条件 + 数据源接入 + 调度器挂载，真实触发验证通过）

**[C] 时间/运行依赖（2 项）**
1. 闭环运行一个季度、卡片 ≥20、周复盘无重大违规
2. 四表快照自动化经季度核验 + BCS 首次评估

### 已知未验证点（运行风险）
- 周六盘中 ticker、真实推送、真实交易时段行为均未实测
- 阶段 3 凯利/压力测试/BCS 为代码就绪，无真实交易样本支撑（trade_records=0）
- .env 凭据存在但未读取验证（[REDACTED]）
- 飞书群 @ 机器人盘中报告：逻辑与生成已验证（mock + 真实库），链路已改项目本体直连（lark-oapi WebSocket，零 Hermes 依赖）；真实群消息触发待 Hermes 侧停用同应用连接后实机验证（见 docs/GATEWAY_STABILITY_ANALYSIS.md）

# traderSystem 代办清单（v2/v3 合并路线）
> 更新：2026-08-15 | 原则：完成一项勾一项。
> 路线：以 v3（成对比价 / 组合风险 / 可验证闭环）为目标态，按 v2 三阶段施工。
> 阶段 1 允许粗糙，不允许跳步；未完成且仍相关的旧项已并入对应阶段。
## 阶段 0：基线补齐（旧项 + 前置数据）
- [x] 实时行情三源可达性与延迟实测（2026-08-15 本机实测）：新浪 hq.sinajs.cn 10-34ms / 腾讯 qt.gtimg.cn 11-59ms / 东财 push2 213-427ms；关键发现：Windows 系统代理（WinINET 127.0.0.1:7892）未运行导致 Python 全部请求被拒，必须 trust_env=False 直连（见 invest/data/realtime.py）
- [x] 实时行情通道：三源直连轮询（新浪 hq.sinajs.cn / 腾讯 qt.gtimg.cn / 东财 push2 多域名容灾）→ 批量取核心池，自动切换备用源并留痕（invest/data/realtime.py + intraday.py 重构）
- [x] 数据新鲜度监控：行情时间戳 vs 接收时间差值写入 job_runs(job='realtime')，stale 计数留痕（log_realtime_health）
- [x] Tushare token 配置（2026-08-15 用户验证）：`.env` 的 `TUSHARE_TOKEN` 已填，实测日线拉取 rows=149 → 备用源 + 日线交叉校验已打通
- [x] 社融增量数据源（2026-08-15 接入）：`macro_china_shrzgm`（商务部源，用户本机实测可达）→ collector 新增 `macro_shrzgm` 任务（1088 行入库），月份统一 YYYY年MM月份，quant_macro 输出「社融增量」（最新 2026-04=6245 亿）；保留新增信贷替代作回落
- [ ] 行业 PE/PB 估值数据源（akshare 乐咕乐股/东财行业估值）→ 估值分位 `quant_valuation.pe_pct/pb_pct`（代码已就绪：industry_valuation.pb 列 + compute_pb_percentile + pipeline 自动合并，见 [A]1）
- [x] 个股→行业映射持久化（2026-08-15）：`data/industry_stocks.json` 手工映射兜底 + `invest/data/industry_map.py`（industry_of/stocks_of/ensure_pool_industries），查询顺序 手工表→候选池
- [x] L3 主题/产业链清单（2026-08-15）：`data/themes.json` 首批 12 个 + `invest/data/themes.py`（find_themes/themes_of_stock）
- [x] 行业全量首跑验证（2026-08-15 用户本机 collect 通过）：industry_all=57060 行，行业数 90（预期 80+）
- [x] 龙虎榜席位类型（2026-08-15 用户验证 + 修复）：`quant_capital.fund_type` 分类链路打通；修复榜单占位行误分类 bug（classify_seat list→None）+ seat_detail 无上榜误报失败 bug；当前候选池仅 000001 且近期无上榜，真实样本待候选池扩充
- [x] 数据回填脚本执行（2026-08-15 用户执行完成）：`scripts/backfill.py 20200101`，daily_bars=1604 行 / index_bars=1604 行 / industry_all=144360 行，个股与行业最早均到 2020-01-02
- [x] 本机跑 pytest 全量（2026-08-15 用户本机执行）：`pytest tests/` EXITCODE=0 全部通过（含 test_data/test_agent/test_api 真实网络用例）
- [ ] FastAPI 接口层（仪表盘先直连 DB，API 后续按需）
## 阶段 1：手工对比闭环（目标：跑通「对比→卡片→交易→复盘」闭环一个季度，卡片累计 ≥ 20 张）
### 1.1 对象池与参照物
- [x] L2 对象池 + 冻结名单（2026-08-15）：pool_rules.py 硬门槛（非ST/上市60日/ADV 5000万）+ freeze/unfreeze/is_frozen
- [x] L3 主题/产业链环节清单（2026-08-15）：`data/themes.json` 首批 12 个 + themes.py 匹配接口（find_themes/themes_of_stock）
- [x] 参照物规则（2026-08-15）：suggest_reference 按可用数据选参照，禁止事后更换
- [x] 对象池硬门槛（2026-08-15）：hard_gate_check + check_and_add（否决自动留痕防选择偏差）
### 1.2 比价与因子 v1
- [x] 因子打分 v1（2026-08-15）：spread.py factor_score 0-5 分制 + 角色权重（错价/修复/风险过滤/背景不占权）
- [x] A/B 主价差（2026-08-15）：spread.py 分位 + 稳健 Z 分（MAD）+ 回归锚区间（40-60%分位）
- [x] 回归锚（2026-08-15）：anchor_range = 历史 40-60% 分位区间（spread_analysis）
- [x] 结构断点检查（2026-08-15）：spread.py 已知断点（config.yaml breaks）+ 统计检测（块中位数差/MAD），行业 PE 与个股价格价差自动截断旧口径防假极值
- [x] 榜单降级为「发现器」（2026-08-15）：mispricing_necessary + discover_eligible，check_and_add(require_mispricing=True) 过错价必要条件才入池，否决留痕
- [x] 因子角色分类（2026-08-15）：ROLE_WEIGHT 错价1.0/修复1.0/风险过滤0.5/背景0.0
### 1.3 机会卡片
- [x] 卡片模板（2026-08-15）：cards 表（schema v8）全字段 + validate_card 完整性校验 + compute_rr 赔率
- [x] 卡片状态机（2026-08-15）：candidate→locked→review→downgraded/void + 非法迁移拦截
- [x] 三句话验证（2026-08-15）：create_card thesis<10字拒绝
- [x] 卡片容量（2026-08-15）：CARD_LIMIT=20 + weakest_card/evict_weakest 自动淘汰
- [x] 赔率刚性顺序（2026-08-15）：compute_rr(目标-入场-成本)/(入场-止损+成本)，负值clamp0
### 1.4 宏观与总闸
- [x] 宏观评级（2026-08-15）：macro_gate.py macro_rating（ratings 表）+ ENV_FACTOR
- [x] 黑天鹅戒断（2026-08-15）：check_black_swan 4 触发 + black_swan_actions 3 动作
- [x] 总仓位闸门 v1（2026-08-15）：position_gate 基准×环境系数×ERP乘数 + apply_gate 含黑天鹅减半
- [x] 环境重评触发条件落地（2026-08-15）：macro_gate.check_env_retrigger 三条件（全A中位PE近10年分位 <0.2/>0.8、社融增量环比转负、10Y 利率周变动>20bp），数据源接入（bond_zh_us_rate 10Y/2Y + stock_a_ttm_lyr 全A PE 分位），挂入调度器 premarket 触发即推送；真实数据验证：社融拐点 52240→6245 亿已正确触发

### 1.5 仓位、计划与执行
- [x] 仓位 v1（2026-08-15）：position.py fixed_risk_position（S/A/B=0.8/0.6/0.35%）
- [x] 单笔硬上限（2026-08-15）：LEVEL_CAP + single_cap（个股10%/ETF15%）
- [x] 交易计划模板（2026-08-15）：create_plan_from_card 引用 card_id + 固定风险建议仓位
- [x] 执行留痕（2026-08-15）：计划/成交偏差 + 周期漂移检测（records.py detect_cycle_drift/drift_report，CYCLE_MAX_DAYS 按周期上限），已挂入周度复盘
- [x] 复盘 v1（2026-08-15）：周度纪律检查 + 持仓卡片复评（weekly.py position_card_review：破止损/近止损/近目标）+ 月度环境质量检查（monthly.py environment_quality：评级稳定性/数据质量）
### 1.6 自动化最小集
- [x] hermes-agent P2 例行简报（2026-08-15 曾挂 21:35）→ **已并入 22:00 evening_report，不再作为现行自动化**（原 pipeline.notify_p2_brief 每日榜单 + 宏观仪表盘）
- [x] 因子快照存档（2026-08-15）：invest/scan.py 每日收盘快照 JSON（阶段 2 PIT 化前置）

### 阶段 1 退出标准
- [ ] 闭环运行一个季度、卡片 ≥ 20 张、周复盘无重大纪律违规
## 阶段 2：扫描自动化 + 组合层最小集

### 2.1 数据底座 PIT 化
- [x] 最小可追溯主键（2026-08-15）：data_provenance 表 + record_provenance 写入（schema v5）
- [x] 数据质量四状态（2026-08-15）：invest/data/pit.py quality_status/quality_report，valid/delayed/stale/conflict 自动检测，挂入 nightly 复盘
- [x] 候选/否决/未执行全量留存（2026-08-15）：candidate_decisions 表，pool.add_to_pool/remove_from_pool 自动留痕
- [x] 快照重建（2026-08-15）：scan.py rebuild_snapshot/rebuild_pool/rebuild_ratings/rebuild_quant，任意历史截面由不晚于该日的最近快照复现
- [x] 历史行业归属/成分/ST 状态按历史时点保存（2026-08-15 v1）：stock_universe_history 表 + invest/data/universe.py（record_universe_snapshot 每日落库 + universe_at/industry_at/st_at 回溯；成分全量回填待数据源成本评估）

### 2.2 价差与因子自动化
- [x] 因子与价差计算自动化（2026-08-15）：invest/discipline/auto.py（auto_price_factors/auto_factor_score/run_pool_automation，对候选池全标的自动打分输出，只报告不自动入池），scripts/run_auto.py factor
- [x] 四套周期镜像全部启用（2026-08-15）：auto.py CYCLE_MIRRORS 波段/配置/事件博弈/趋势 四套镜像（参照年数/错价阈值/最大持有），run_pool_automation 全量启用
- [x] 共线性控制（2026-08-15）：invest/quant/collinearity.py 因子相关矩阵 + |ρ|>0.60 违规对检测 + weight_adjustment 降权
- [x] 拥挤度状态机（2026-08-15）：invest/quant/crowding_state.py 五态判定（分位+量能趋势），已接入 pipeline quant 写入 quant_valuation.crowding_state（schema v6）

### 2.3 组合风险最小集
- [x] 风险簇映射 v1（2026-08-15）：invest/discipline/clusters.py 12 簇手工规则表 + 行业自动打标（config.yaml clusters 可覆盖）
- [x] 跨周期敞口合并（2026-08-15）：merge_cross_cycle 同标的跨周期仓位合并（weight 求和 + cycles 记录）
- [x] 组合预算上限落地（2026-08-15）：exposure_report/check_cluster_budgets 风险簇 40%/风格 60%/事件 20% 违规检测（L2 软硬上限 25/35 预留）
- [x] 相关性-共同因子检查（2026-08-15）：手工规则表按经济驱动归簇（如银行同时入高股息/金融地产），不依赖历史相关性
### 2.4 执行与成本
- [x] 成本模型（2026-08-15）：invest/discipline/costs.py 逐笔佣金/印花税/过户费/滑点/冲击成本，record_cost 写入成交留痕
- [x] T+1/涨跌停（ST±5%/北交所±30%）/ADV 参与率开仓前校验（2026-08-15，costs.check_tradable）
- [x] 止损无法成交闭环（2026-08-15）：mark_liquidity_breach 标记 + risk_rules 冻结记录 + check_position 拒绝新开仓
### 2.5 自动化升级
- [x] 定时扫描自动化（2026-08-15）：invest/scan.py 每交易日收盘后写入 data/snapshots/<date>.json（候选池/评级/四表摘要），挂入调度器 after_close
- [x] P1 推送（2026-08-15）：scan.py 变化检测（新入池/等级升降/评级变化）→ [P1] 企业微信，600s 限频，无变化不推
- [x] P0 监控（2026-08-15）：invest/monitor.py 持仓止损/证伪 + 数据冲突主动监控，挂入调度器 intraday_tick，30 分钟限频
- [x] 实时行情通道：三源直连轮询（新浪 hq.sinajs.cn / 腾讯 qt.gtimg.cn / 东财 push2）→ 3-5 秒间隔、批量取核心池，替代「东财盘口 + 新浪 60 分钟线兜底」的现状（2026-08-15，invest/data/realtime.py）
- [x] 数据新鲜度监控：行情时间戳 vs 接收时间差值入库（job_runs job='realtime'），超阈值（>10 秒）即时告警；延迟/断线自动切换备用源并留痕（2026-08-15）
- [x] 数据失效即防守：新鲜度不合格的行情不得支撑 P0 决策，相关机会禁止新开仓（2026-08-15：realtime_health 查询 + Agent query_realtime_health 工具 + TRADE prompt 硬约束）
- [x] 推送时效分级与非交易时段规则（v3 14.3，2026-08-15）：P0(core 立即 300s 限频)/P1(track 600s 降频)/P2(rest 仅晚间汇总)；非交易时段盘中异动静默
- [x] 自动化降级规则（2026-08-15）：data_guard 数据失效/日线陈旧→禁止新开仓；check_position 增加 data_ok 参数（invest/discipline/risk.py）
### 2.6 权重治理
- [x] 四套权重冻结（2026-08-15）：invest/governance.py freeze_weights 快照 rating_position_map+indicators 四套为 frozen 基准（真实库已冻结 v1.0）
- [x] 季度样本外评估（2026-08-15）：quarterly_oos_eval top N 行业 N 日超额收益 + win_rate + 重叠样本提示（需先回填历史 quant 数据）
- [x] 规则版本管理（2026-08-15）：rule_versions 表（schema v7）+ freeze/rollback/params_for 全字段留痕
### 阶段 2 退出标准
- [ ] 四表与快照不再依赖手工整理，P0/P1 推送经一个季度核验，BCS 完成首次评估

## 阶段 3：进化与验证
- [x] 因子有效性检验数据化（2026-08-15）：backtest/factor_eval.py 滚动 IC（Spearman）/ICIR/分组单调性，纯 numpy 实现；scripts/eval_factors.py 真实数据评估（实测 20 日动量 IC=0.021 ICIR=0.06 → 判无效）
- [x] 归因体系（2026-08-15）：invest/review/attribution.py 五维切片（n/mean/win_rate/total_pnl）+ 亏损集中度 top_losers
- [x] 错误分类（2026-08-15）：invest/review/error_classify.py 五类自动分类 + 汇总报告 + 分类改进建议
- [x] 凯利启用条件（2026-08-15）：invest/discipline/kelly.py wilson_lower + kelly_fraction + kelly_capped(1/6)，格子决策（n>=20 且凯利>0 才启用）
- [x] 固定风险→凯利切换（2026-08-15）：kelly_decision 不合格回退固定风险、合格启用置信下界凯利×1/6；evaluate_grid 从 trade_records 统计格子样本
- [x] 回撤/损失限额（2026-08-15）：invest/discipline/limits.py 阶梯 warn5%/reduce8%/clear12%/halt15% + 单日2%/单周4% 禁开仓（risk.py drawdown_stage 已接入）
- [x] 压力测试（2026-08-15）：limits.stress_test 5 场景（低开5%/主板跌停/创业板跌停/相关性0.8/流动性减半）+ worst_scenario
- [x] BCS/VMS 双百分制评估（2026-08-15）：invest/review/bcs.py 回测完整性/验证成熟度评分 + veto_check 一票否决（实时行情/数据陈旧/无止损/计划外交易/池超限）；真实评估 VMS=100A、BCS=20D（无交易样本）
- [x] 年度复盘升级（2026-08-15）：yearly.py 等级单调性 + 凯利校准 + 权重区分度 + rule_changes 归档 + 错误分类汇总（保留 backtest_summary 兼容）
## 已确认决策（勿忘）
- 主复权口径 = 新浪 qfq（2026-08-03）
- 不自动交易；规则先回测后上线；核心关注 ≤ 10；候选池 ≤ 20；仅 A 股
- 人可否决系统候选，不得临时加入名单外机会或修改参数美化卡片（v3 3.2）
- 宏观只做减法：宽松/中性系数 1.00、收紧 0.70，不给方向加分（v3 6.2）
- 仓位无合格样本时用固定风险，不填假设胜率进凯利（v3 11.6）
- 同一底层标的/同 L2/同风险簇的跨周期仓位必须合并计算（v3 11.2）
- 实时数据硬约束（2026-08-15，轮询频率 2026-08-18 降为 10s）：盘中行情必须用 Level-1 快照接口直接轮询（新浪/腾讯/东财三源，**10 秒间隔**），端到端延迟 ≤ 10 秒；分钟线只做历史回填，不得作为实时兜底；任何延迟或失效数据不得支撑 P0 决策，必须先告警；调度器已实现（2026-08-15）：intraday_tick 每 10 秒轮询（2026-08-18 由 4s 降频），非交易时段守护，正常轮询不写 job_runs（留痕由 log_realtime_health 节流承担，正常 60s 一条基线、异常立即记）

## 已完成（历史）
- [x] 数据层：涨停池/炸板池（2026-08-04）、同花顺行业全量扩展（2026-08-04）、回填脚本（2026-08-04）、个股多标的采集与盘中异动监测（2026-08-04）
- [x] 定量层：行业 RS/多周期动量/趋势阶段、板块轮动、市场温度 v1、资金属性 v1、行业联动 v1、中轨线（周线趋势/拥挤度/宏观流动性）、趋势阶段/风格/风格×温度校准（2026-08-03）、回测框架 + 评级-仓位映射（2026-08-04）
- [x] 推理：LLM 接入（DeepSeek，2026-08-03）、双 Agent + 工单 + 仲裁 v1、观点库 CRUD + 五要素校验 + 准确率 v1（2026-08-03）
- [x] Agent 升级（2026-08-16）：融入 A-Stock-Skills 分析流程——6 步分析流程（数据核实→多维度交叉验证→技术面辅助→筛选条件→四段式报告→问责机制）+ 硬约束强化；新增 cross_validate 工具（行业/个股四维度：强度/资金/联动/估值一次性汇总），prompt 含 trade-journal 问责理念，tests/test_agent.py 增 2 用例
- [x] 报告改版：短线/中期分工（2026-08-16）：日报+盘中实时报告聚焦短线操作辅助——异常波动检测（量比/振幅/长上影下影→做T信号）、做T提示（实时价日内位置低吸/高抛）、建仓时机提示（情绪周期+温度+低估值启动）；周报聚焦中期（中线强度前8/低估值趋势候选/宏观流动性）；invest/report.py + tests/test_report_short.py (6)
- [x] 盘前信息早报（2026-08-16）：交易日 8:40 发送 morning_brief_report（invest/report.py）——隔夜市场一句话（温度/情绪周期/市场风格）、龙虎榜净买入 TOP5 资金焦点、板块主线（强度+涨幅）、今日关注（候选池/异常波动）、评级仓位、宏观速览，简明扼要（<30 行）；调度器新增 morning_brief 任务；**仅发送飞书群**（notify_morning_brief 直连 feishu_push，不走 Notifier 多通道）；tests/test_morning_brief.py (4)
- [x] 高星量化项目落地 4 项（2026-08-16）：① Alpha158 核心因子（invest/quant/alpha158.py 73 因子，纯 pandas 不依赖 qlib，接 pipeline + scripts/eval_alpha158.py + test_alpha158.py）；② kill-gate 击杀门禁（invest/discipline/kill_gate.py：最大回撤/连亏/盈利因子/胜率/最小样本硬门槛，挂入 BCS full_assessment，test_kill_gate.py）；③ youzi-trading-skill（23位游资心法 SKILL.md 已构建 tools/hermes_skills/youzi-trading/，待复制到 Hermes skills/finance/）；④ quantdash-ai-stock 对照（docs/QUANTDASH_COMPARISON.md）+ 情绪周期状态机落地（invest/quant/emotion_cycle.py 冰点/启动/主升/退潮，接入日报，test_emotion_cycle.py）
- [x] 盘后报告合并 + 数据新鲜度门禁（2026-08-18）：盘后日报(16:00)/P2简报(21:35)/每日复盘(22:00) 三份合并为一份 **22:00 晚间盘后报告**（evening_report：daily_report + 复盘统计 + 数据质量）；发送前用 _data_lag_reason 校验日线/指数是否到最近交易日，**滞后则不发送、推送原因**（12h 限频 + job_runs 留痕）；16:00 after_close 保留采集/Agent/仲裁/收盘扫描/历史快照（不再推送日报）；21:30 行业刷新 + 21:40 日线补采保留为数据准备；tests/test_pipeline.py 新增门禁用例 2 个
- [x] 盘中异动通知降频（2026-08-21）：单只个股盘中异动**30 分钟最多通知一次**——core 限频 180s→**1800s**（track 1800s 不变），P0/P1 统一按标的独立限频（key=intraday_{symbol}，通知后 30 分钟内不再通知）；LLM 归因同限频（30 分钟内每标的最多归因一次，进一步省 token）；intraday.py _PUSH_POLICY + docstring、SYSTEM_GUIDE/OPERATIONS 同步；tests/test_intraday.py +1 回归用例
- [x] 修复 intraday 归因爆 token（2026-08-21）：查实今日 intraday=2,088,800 token（前几日 6-7 万）——**LLM 归因 _attribute 在发送限频之前无条件执行**：盘中核心池票持续触发异动阈值，每 10s tick 检测到就调一次归因（180 次异动≈180 次 LLM 调用，每次约 1.2 万 token）；修复——归因与发送**同 key 同 interval 限频**（_attr_limited，P0 180s/P1 1800s 内每标的最多归因一次）；tests/test_intraday.py +1 回归用例；279 全绿
- [x] run_skill 异步化（2026-08-21）：深度分析不再阻塞飞书线程——注册 sink（feishu_ws 启动时）后 run_skill 改后台 daemon 线程执行、立即返回"已启动（约5-20分钟）"，完成回调把报告摘要+路径发回原会话（thread-local chat_id）；飞书层加"深度分析/UZI"请求系统级 ack（60s 限频）；实测异步链路（立即返回 + 后台回调正确）；修复原因：模型选 --depth deep 同步等 20 分钟致用户无回复；tests/test_web_skill.py +2 用例
- [x] 安装通用工程 Skill（2026-08-21）：**brainstorming**（obra/superpowers，头脑风暴→设计文档 docs/superpowers/specs/）、**grill-me + grilling**（mattpocock/skills，拷问式需求对齐，grill-me 为调用 grilling 的壳）、**systemdebugging**（mattpocock diagnosing-bugs，系统化 Bug 诊断循环 minimise→hypothesis→instrument→fix→regression）——装入 .claude/skills/，AGENTS.md 增加说明
- [x] 修复 Agent 回复工具 JSON + web_search 参数冲突（2026-08-21）：① **build_dispatch 不再对 web_search/web_fetch/run_skill 绑定 conn**（partial(conn) 导致 query 参数冲突报 multiple values）；② **LLMClient.run 加总结兜底**——轮数耗尽且最后一条是工具结果时，追加一次无 tools 的"请总结"调用，不再把工具 JSON 当最终回复（run_chat max_turns 3→4）；实测"分析 600519"返回正常模型文本（行情+多维+结论+Skill 标注）；tests/test_web_skill.py 新增 dispatch 无 conn 绑定 + 总结兜底 2 用例
- [x] Agent 联网检索 + 完整 Skill 流水线（2026-08-21 完结）：① **web_search/web_fetch**（必应 cn 无 key、trust_env=False）——飞书 Agent 查最新资讯/财报/新闻；② **run_skill 跑通 UZI deep-analysis 完整流水线**（600519 实测出 697KB HTML 报告，综合评分 52.4/100）——注入 DeepSeek OpenAI 兼容凭据、UZI_LEGACY=1 老路径规避受限环境 multiprocessing 管道、lite/medium/deep 超时 600/900/1200s、解析报告路径+摘要；**补丁 UZI 上游 2 个 bug**（run_idea_screen None 值清洗 moat_total、special_cards similar_stocks 形状兜底）；③ 注册工具 + CHAT_SYSTEM 规则 1/2/9；tests/test_web_skill.py 4 用例；274 测试全绿 + ruff 全绿；⚠️ lite 实际约 5-10 分钟（LLM 多轮+59 项渲染），飞书同步等待较长，异步补发报告列为后续优化
- [x] 静态检查 + 分析skill指南 + 隔夜外围进早报（2026-08-21）：① **ruff 全绿**（ruff.toml 忽略项目约定项 DTZ/S110/BLE001），存量 319 项清零（含修 4 处 int(None) 潜在崩溃点：store/tickets/pit lastrowid、emotion NaN 自比较改 math.isnan、scheduler callable→Callable、test_report 误伤恢复）；mypy 配置就绪（mypy.ini，渐进式），**剩余 35 个类型债见下方改进清单**；② docs/ANALYSIS_SKILLS_GUIDE.md：4 个金融 skill 方法论+适用场景+已接入通道（飞书 CHAT_SYSTEM 已内置），AGENTS.md 增加静态检查节；③ **隔夜外围进盘前早报**（global_snapshot.py：新浪 gb_ 美股/hf_ 期货富时A50/商品 + 腾讯汇率，实测 道指-0.75%/黄金+0.07%/USDCNY 6.7240），tests/test_pools.py 新增用例
- [x] 静态检查 + 分析skill指南 + 隔夜外围进早报（2026-08-21）：① **ruff 全绿**（ruff.toml 忽略项目约定项 DTZ/S110/BLE001），存量 319 项清零（含修 4 处 int(None) 潜在崩溃点：store/tickets/pit lastrowid、emotion NaN 自比较改 math.isnan、scheduler callable→Callable、test_report 误伤恢复）；mypy 配置就绪（mypy.ini，渐进式），**剩余 35 个类型债见下方改进清单**；② docs/ANALYSIS_SKILLS_GUIDE.md：4 个金融 skill 方法论+适用场景+已接入通道（飞书 CHAT_SYSTEM 已内置），AGENTS.md 增加静态检查节；③ **隔夜外围进盘前早报**（global_snapshot.py：新浪 gb_ 美股/hf_ 期货富时A50/商品 + 腾讯汇率，实测 道指-0.75%/黄金+0.07%/USDCNY 6.7240），tests/test_pools.py 新增用例
- [x] mypy 存量类型债改进清单（2026-08-21，35 项，渐进式清理，不阻塞）：invest/quant/indicators.py(10) 主要为 numpy 泛型重载；invest/scheduler.py(7) _wrap/回调类型；invest/agent/llm.py(7) OpenAI 响应字段；invest/agent/tools.py(5) dict 泛型；invest/viewpoints/store.py(3)；invest/review/error_classify.py(2)；invest/data/{collector,realtime,backfill}.py 各 1；invest/monitor.py(1)
- [x] 涨停连板梯队 + 板块主力资金（2026-08-20，a-share-market-data 流程落地）：① 涨停/炸板池**个股明细**（东财 push2ex getTopicZTPool/ZBPool：代码/名称/连板/首封/封单/炸板标记，emotion.fetch_limit_up_pool）→ 新表 limit_up_pool；② 行业板块主力资金（**东财 push2delay clist f62**，akshare 封装被限流改手写，fund_flow.py）→ 新表 sector_fund_flow；③ 盘中 ticker 每 5 分钟节流拉取落库（_tick_collect_pools，留痕 pool_snapshot）；④ 盘中报告新增【连板梯队·涨停龙头】【资金主线·主力净流入TOP3】段（brief/完整版都有）；实测落库 涨停池125 只（金健米业4板）+ 板块资金90 行业；tests/test_pools.py 新增 3 用例
- [x] 数据新鲜度校验 + 爱心艾特限定 + 收盘快照提速（2026-08-20）：① Agent 新增 query_data_freshness 工具（daily_bars/index_bars/quant 时点 vs 最近交易日），CHAT_SYSTEM 强制"回答前先验数据、滞后先说明再答，不用过时数据"；② ❤️ 表情仅**艾特机器人/私聊**才回（_should_react，普通群消息不回）；③ **收盘快照**——交易日 16:10 snapshot_close 任务用实时源直接写当日收盘价（核心池三源快照→daily_bars、腾讯指数快照 9 个→index_bars，src='snapshot'），不必等 akshare 日线晚间发布；实测 16:10 后即可查当天收盘；tests 新增 freshness/snapshot/_should_react 用例
- [x] LLM 取消预算拦截改用量告警（2026-08-20）：全部 job 不再返回"[预算不足]"、输出无限制（run_chat/research/trade 去掉 max_tokens）；改为**两个全局告警**——单次调用超 20,000 tokens（1h 限频）与当日累计超 500,000 tokens（每天一次）通过 Notifier 推送，状态存 data/llm_alert_state.json；llm.py 移除 _budget_ok；tests/test_agent.py 改为告警用例（单次/日总量/限频）
- [x] 修复飞书"预算不足跳过推理"（2026-08-20）：根因 feishu_chat 计入全局 6 万/日预算，管理员高频对话撞线；修复——飞书会话类 job（feishu_chat/group）跳过全局预算（管理员不限、非管理员由 100 万限额把关），其余定时 job 仍受 6 万保护；单次调用瘦身——run_chat max_tokens=1200、run_research/trade max_tokens=2000（防模型吐超长文撑爆上下文，此前单次调用曾达 6.1 万 token）；tests/test_agent.py 新增预算放行用例
- [x] 默认 ticker-only + Skill 大模型自选 + 报告 A/B/C/E（2026-08-19）：① run_service **默认 ticker-only**（--full 才完整 APScheduler），重启指令默认带 --ticker-only；② Skill 改**大模型语义自选**——CHAT_SYSTEM 内置 serenity/youzi/stock_analysis 方法论，模型回复末尾自标注「↘ 已使用 Skill：xxx」，删除关键词路由（route_skill）；③ 报告A：盘中报告加**今日操作建议**（温度+情绪周期）；④ 报告B：短线失效条件去 RS 化（价格/量能/情绪类，RS 仅中长线）；⑤ 报告C：日报详细化——宏观流动性→温度/情绪→板块→**重点关注行业**（FOCUS_INDUSTRIES 名单+四维数据+LLM 意见）→强度→异动→候选池→**消息面（大模型提炼）**→持仓警戒→Agent复盘；⑥ 报告E：盘中报告**默认简洁版**（brief），含「详细/完整」才发完整版（私聊/群聊一致）；tests 更新（skill 机制/简洁版/重点行业缺省跳过）
- [x] 板块异动阈值 + 周期化日线 + Skill 路由 + 爱心表情（2026-08-18）：① 异动阈值按板块——主板 ±3%、创业板(300/301)/科创板(688/689) ±6%（intraday._move_threshold，推送带阈值标注，test_intraday 更新）；② query_stock_daily 按周期拉取——短线/游资=60 日、中线=250、长线=500（days 上限放宽到 750，tool 描述与 CHAT_SYSTEM 指导周期）；③ **Skill 路由**——产业链/基本面→Serenity、短线/异动/游资→youzi、五步法→stock_analysis（agents.route_skill 本地关键词零 token，SKILL_LIBRARY 摘要注入 prompt），回复末尾斜体行标注「↘ 已使用 Skill：xxx」（飞书消息不支持小字号，用斜体弱化近似；feishu_push.send_post 富文本）；④ **收到消息先回 ❤️ 表情**（feishu_push.add_reaction，im:message.reaction 权限，需后台开启）；实测路由正确；tests 新增 route_skill/skill 标注用例
- [x] 个股日线按需查询工具（2026-08-18）：Agent 新增 query_stock_daily——本地 daily_bars 优先（候选池个股），本地缺失（池外个股如 600519）按需 akshare 联网拉取（东财→新浪双源回退，30 分钟缓存），返回 最新收盘/1/5/20日涨跌幅/60日高低/最近5条K线；修复：_daily_stats 解包顺序、新浪 datetime 日期过滤、run_chat max_turns 2→3（防连续两轮工具调用后返回工具 JSON 而非结论）；CHAT_SYSTEM 明确"分析个股先查日线，不要再回没数据"；tests/test_agent.py 新增用例；实测 600519 收盘 1307.88
- [x] 非交易时段误报"数据失效"修复（2026-08-18）：query_realtime_health 改为**交易时段感知**——休市时实时行情旧属正常，返回 ok=True 并提示用日线/收盘数据，不再拒绝盘后/盘前个股分析；Agent 提示词（_ANALYSIS_PROCESS/_COMMON_RULES/CHAT_SYSTEM）同步加"数据失效仅交易时段适用"限定；实测非交易时段分析 600519 正常出结论；tests/test_agent.py 新增用例
- [x] 飞书私聊 + 群内全量 Agent 回应（2026-08-18）：① **私聊（p2p）任意消息回应**（修复私聊不回应）；② **群内 @ 任意消息由 Agent 回应**（不再只回报告）：语义报告→盘中报告（非管理员公开版）、问候→帮助提示（本地判定零 token）、其他→会话 Agent（invest.agent.run_chat，带系统数据工具，max_turns=2）；③ 非管理员（私聊/群内@）统一 100 万/日限额（llm_usage job='group'，llm._budget_ok 对 group 放行由限额把关）；限频 报告30s/会话10s；tests/test_feishu_ws.py 新增私聊/会话用例
- [x] 群成员开放 + 限额 + 省 token + OS 计划任务（2026-08-18）：① 非管理员艾特可获取**公开版**盘中报告（intraday_report public=True，无持仓警戒），每日 token 限额 100 万（FEISHU_NONADMIN_DAILY_TOKEN_LIMIT，llm_usage job='group' 记账，超限回额度提示）；② 周报消息面改**大模型提炼**（财联社电报为素材 + LLM 挑讨论度/重要性最高的 5 条并给理由，失败回退直列）；③ 盘中报告**去指数/风格**，新增 板块异动+情绪人气+龙虎榜龙头（纯 DB 零 token）；Agent prompt 加省 token 规则（近60日/关键字段/工具≤3次/单观点≤80字）+ max_turns 5→4；④ **OS 计划任务**：scripts/run_job.py 单任务入口 + scripts/install_os_tasks.ps1 注册 9 个 Windows 任务（schtasks /XML，StartWhenAvailable 错过补跑）+ run_service.py --ticker-only（10s 轮询仍需常驻）；tests 新增 public/限额/ticker_only 用例
- [x] 盘中降频 + 边沿告警 + 语义触发（2026-08-18）：① 周末周报改**周日 20:00**（原周六 09:00），weekly_report 新增**消息面**（财联社电报 stock_info_global_cls 近 7 日 TOP6）；② 盘中轮询 4s→**10s**，异动推送限频 P0: 600→**180s**、P1: 1800s 不变（intraday.py _PUSH_POLICY）；③ P0 数据失效告警改**边沿触发**（失效通知一次、恢复再通知一次，状态存 data/monitor_state.json，monitor.py）；④ 飞书盘中报告**去关键词触发，纯 LLM 语义识别**（feishu_ws._is_report_request 纯语义，LLM 失败→False；tests/test_feishu_ws.py 更新）；tests/test_monitor.py 边沿触发用例
- [x] 网关稳定性 + 去 Hermes 化（2026-08-18）：① 根因分析（docs/GATEWAY_STABILITY_ANALYSIS.md）——Hermes 与本项目共用同一飞书应用长连接导致消息随机分流（最大根因），叠加纯 LLM 意图判定单点静默、无 @ 识别、无 token 缓存/重试；② feishu_ws 加固：@ 提及识别（MentionEvent/UserId 对象兼容）、关键词规则+LLM 双保险、管理员 ack、30s 限频、非管理员权限提示；③ feishu_push：tenant token 2h 缓存 + 发送重试 1 次；④ 微信推送去 Hermes：context-tokens 迁入 data/weixin/（migrate_context_tokens 一次性迁移），默认不再读 E 盘 Hermes 目录；⑤ scripts/disable_hermes_feishu.ps1（停用 Hermes 同应用飞书连接）；⑥ 去 Hermes 迁移记录 + 不可替代清单（docs/HERMES_FREE_MIGRATION.md）
- [x] 调度器（2026-08-04）、企业微信推送（2026-08-04）、仪表盘 Streamlit（初版 6 页，现 **9** 页含中期比价）
- [x] 复盘引擎（周/月/年，2026-08-04）
- [x] 工程：依赖安装、.env 密钥、pytest.ini（2026-08-03/04）
- [x] 实时行情三源直连轮询（2026-08-15）：新浪 hq.sinajs.cn / 腾讯 qt.gtimg.cn / 东财 push2 多域名容灾；批量取核心池；自动切换；延迟/新鲜度监控留痕（invest/data/realtime.py、intraday.py 重构、tests/test_realtime.py）
- [x] 报告体系优化 + 盘中实时报告机器人（2026-08-15）：invest/report.py 盘后日报/盘前清单/盘中实时报告（温度倾向/评级仓位/强度解读/候选池变化/持仓警戒），pipeline.notify_* 改用新模板；feishu_group_watch.py 扩展——飞书群艾特机器人/发关键词自动回复盘中实时报告（保留群监控转发），tests/test_report.py (5)
