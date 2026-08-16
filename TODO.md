## 📋 完成度与测试覆盖追踪（2026-08-15 盘点）

### 总体进度
- 总项 83 | ✅ 已完成 73 | ⬜ 未完成 10（[B] 需用户执行 8 + [C] 时间/运行依赖 2）
- [A] 类「代码可做 12 项」已于 2026-08-15 全部落地（含代码侧完成、数据源仍待接入的项）
- pytest 全量（排除需外部 key 的 test_data/test_agent/test_api）: **153 passed**
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
| 仪表盘 Streamlit 6 页面（2026-08-04） | test_dashboard.py (6) 存在但未纳入全量 pytest（依赖窗口环境，未验证） |
| 企业微信推送 / 飞书通道 | test_pipeline.py 覆盖 mock 逻辑；**真实推送未做端到端验证**（需 webhook） |
| 龙虎榜/两融/宏观采集（2026-08-04） | 真实库已验证入库，但 test_data.py (28) 被排除（依赖真实网络/慢） |
| 收盘扫描 P1 推送（scan.py） | 快照逻辑有测试；**推送发送链路未真实触发**（无变化时静默） |
| scheduler 8 jobs | test_pipeline.py 断言 job 注册；**盘中 4 秒 ticker 未在真实交易时段跑过**（周六无法验证） |
| 行业估值采集（2026-08-15 修复） | 真实采集 293 行入库验证；无独立单测（依赖网络） |

### ⬜ 未完成 10 项分类明细

**[A] 代码可做（12 项）— 已于 2026-08-15 全部完成**
1. ✅ 行业 PE/PB 估值分位：代码就绪（pb 列 + compute_pb_percentile + pipeline 合并）；数据源接入仍属 [B]
2. ✅ 个股→行业映射持久化（data/industry_stocks.json 手工映射兜底 + industry_map.py）
3. ✅ L3 主题/产业链清单（data/themes.json 首批 12 个 + themes.py）
4. ✅ 结构断点检查（spread.py 已知断点 + 统计检测，截断历史窗口防假极值）
5. ✅ 榜单降级为「发现器」（mispricing_necessary + check_and_add require_mispricing）
6. ✅ 执行留痕：计划/成交偏差 + 周期漂移检测（records.py detect_cycle_drift）
7. ✅ 复盘 v1：周度纪律+持仓卡片复评；月度环境质量检查
8. ✅ hermes-agent P2 例行简报（pipeline.notify_p2_brief + 调度器 21:35）
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
- 飞书群 @ 机器人盘中报告：逻辑与生成已验证（mock + 真实库），真实群消息触发链路依赖 Hermes 桌面端 gateway.log，需实机群内验证

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
- [x] hermes-agent P2 例行简报（2026-08-15）：pipeline.notify_p2_brief（每日榜单 + 宏观仪表盘），挂入调度器 21:35（行业刷新后、复盘前）
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
- 实时数据硬约束（2026-08-15）：盘中行情必须用 Level-1 快照接口直接轮询（新浪/腾讯/东财三源，3-5 秒间隔），端到端延迟 ≤ 10 秒；分钟线只做历史回填，不得作为实时兜底；任何延迟或失效数据不得支撑 P0 决策，必须先告警；调度器已实现（2026-08-15）：intraday_tick 每 4 秒轮询，非交易时段守护，正常轮询不写 job_runs（留痕由 log_realtime_health 节流承担，正常 60s 一条基线、异常立即记）

## 已完成（历史）
- [x] 数据层：涨停池/炸板池（2026-08-04）、同花顺行业全量扩展（2026-08-04）、回填脚本（2026-08-04）、个股多标的采集与盘中异动监测（2026-08-04）
- [x] 定量层：行业 RS/多周期动量/趋势阶段、板块轮动、市场温度 v1、资金属性 v1、行业联动 v1、中轨线（周线趋势/拥挤度/宏观流动性）、趋势阶段/风格/风格×温度校准（2026-08-03）、回测框架 + 评级-仓位映射（2026-08-04）
- [x] 推理：LLM 接入（DeepSeek，2026-08-03）、双 Agent + 工单 + 仲裁 v1、观点库 CRUD + 五要素校验 + 准确率 v1（2026-08-03）
- [x] Agent 升级（2026-08-16）：融入 A-Stock-Skills 分析流程——6 步分析流程（数据核实→多维度交叉验证→技术面辅助→筛选条件→四段式报告→问责机制）+ 硬约束强化；新增 cross_validate 工具（行业/个股四维度：强度/资金/联动/估值一次性汇总），prompt 含 trade-journal 问责理念，tests/test_agent.py 增 2 用例
- [x] 高星量化项目落地 4 项（2026-08-16）：① Alpha158 核心因子（invest/quant/alpha158.py 73 因子，纯 pandas 不依赖 qlib，接 pipeline + scripts/eval_alpha158.py + test_alpha158.py）；② kill-gate 击杀门禁（invest/discipline/kill_gate.py：最大回撤/连亏/盈利因子/胜率/最小样本硬门槛，挂入 BCS full_assessment，test_kill_gate.py）；③ youzi-trading-skill（23位游资心法 SKILL.md 已构建 tools/hermes_skills/youzi-trading/，待复制到 Hermes skills/finance/）；④ quantdash-ai-stock 对照（docs/QUANTDASH_COMPARISON.md）+ 情绪周期状态机落地（invest/quant/emotion_cycle.py 冰点/启动/主升/退潮，接入日报，test_emotion_cycle.py）
- [x] 调度器（2026-08-04）、企业微信推送（2026-08-04）、仪表盘 Streamlit 6 页面（2026-08-04）
- [x] 复盘引擎（周/月/年，2026-08-04）
- [x] 工程：依赖安装、.env 密钥、pytest.ini（2026-08-03/04）
- [x] 实时行情三源直连轮询（2026-08-15）：新浪 hq.sinajs.cn / 腾讯 qt.gtimg.cn / 东财 push2 多域名容灾；批量取核心池；自动切换；延迟/新鲜度监控留痕（invest/data/realtime.py、intraday.py 重构、tests/test_realtime.py）
- [x] 报告体系优化 + 盘中实时报告机器人（2026-08-15）：invest/report.py 盘后日报/盘前清单/盘中实时报告（温度倾向/评级仓位/强度解读/候选池变化/持仓警戒），pipeline.notify_* 改用新模板；feishu_group_watch.py 扩展——飞书群艾特机器人/发关键词自动回复盘中实时报告（保留群监控转发），tests/test_report.py (5)
