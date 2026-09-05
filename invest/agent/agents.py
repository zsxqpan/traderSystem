"""双 Agent：投研 / 交易。

2026-08-16 升级：融入 A-Stock-Skills 的分析流程（多源交叉校验、技术信号
体系、筛选条件、报告结构、trade-journal 问责机制、watchlist 监控思维），
同时保留系统硬约束（数据失效即防守、周期标签、禁止编造、只引用工具数据）。

核心原则（来自 A-Stock-Skills 与 v3 决策，勿违背）：
1. AI 不可信，除非愿意被复盘——每个观点必须带失效条件，事后可验证；
2. 数据多源交叉：单一数据源结论不可靠，用不同维度（强度/资金/联动/估值）交叉验证；
3. 只引用工具返回的数字，禁止凭空编造；
4. 数据失效即防守：行情不新鲜时禁止 P0 决策。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime

from .llm import LLMClient
from .tools import TOOL_SCHEMAS, build_dispatch

# 孤立 6 位代码；排除 YYYYMMDD（如 20240828）避免把日期当股票
_CODE_RE = re.compile(r"(?<!\d)(?!(?:19|20)\d{6}(?!\d))(\d{6})(?!\d)")
_EV_ID_RE = re.compile(r"\[?(ev_\d+)\]?")
_NUM_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)")
_NEWS_TOOLS = {
    "web_search", "web_fetch", "xueqiu_search",
    "xueqiu_fetch_article", "xueqiu_fetch_user",
}
_QUOTE_KW = ("现价", "实时价", "实时报价", "最新价", "涨跌幅", "多少钱", "现在多少", "报价")
_GREET_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|在吗|在么|谢谢|早安|早上好|晚安|hello|hi)[\s!！。.?？~～]*$",
    re.IGNORECASE,
)
_EVIDENCE_TOPIC_KW = (
    "现价", "实时价", "实时报价", "最新价", "涨跌幅", "行情",
    "财报", "半年报", "年报", "季报", "中报", "业绩", "公告", "新闻", "消息", "营收", "净利",
)
_EXPLAIN_RE = re.compile(r"(什么是|是什么意思|什么意思|解释一下|含义|意思是|怎么理解)")
_BARE_NEWS_RE = re.compile(r"^有(什么|啥)?(消息|新闻)[吗么]?[?？]?$")
_REPLY_MSG_RE = re.compile(r"(帮我)?回(复|一下)?这(条|个)?(消息|新闻)")
_ALLOWED_NUM_KEYS = frozenset({
    "price", "prev_close", "latest_close", "close", "open", "high", "low",
    "high_60d", "low_60d",
    "pct", "pct_percent", "pct_1d", "pct_5d", "pct_20d",
    "amount", "vol", "volume", "vol_ratio", "main_net", "super_net",
    "buy", "sell", "net",
    "text", "snippet", "title",
})
_INDEX_HINT = ("上证", "指数", "沪指", "大盘")
_AS_OF_LEAD = ("截至", "截止", "止于")
_PUBLISH_HINT = re.compile(r"(发布|披露|刊登|发表|讯)")
# 名称 → (代码, obj_type)，长名优先匹配；classify_intent / resolve_quote_ref 共用
_NAME_SYMBOL: tuple[tuple[str, str, str], ...] = (
    ("贵州茅台", "600519", "stock"),
    ("茅台", "600519", "stock"),
    ("宁德时代", "300750", "stock"),
    ("宁德", "300750", "stock"),
    ("比亚迪", "002594", "stock"),
    ("上证指数", "000001", "index"),
    ("上证", "000001", "index"),
    ("深证成指", "399001", "index"),
    ("深成", "399001", "index"),
    ("沪深300", "000300", "index"),
    ("创业板指", "399006", "index"),
    ("创业板", "399006", "index"),
    ("科创50", "000688", "index"),
    ("科创", "000688", "index"),
)
_STATUS_KW = ("系统状态", "任务状态", "数据新鲜", "新鲜度", "采集成功", "采集是否", "调度状态")
_FILING_KW = ("半年报", "一季报", "三季报", "季报", "中报", "年报", "财报", "业绩")
_SECTOR_NAMES = (
    "半导体", "新能源", "白酒", "银行", "券商", "有色", "煤炭",
    "钢铁", "化工", "地产", "军工", "医药", "消费", "中概",
)
_REPORT_NEG = (
    "历史", "上周", "上月", "上个月", "去年", "周报", "月报", "年报", "季度报",
    "回测", "模拟", "新闻", "政策", "财报", "公告", "研报", "复盘总结",
)

# 对话输入统一上限（历史/落库/飞书入口共用）
CHAT_INPUT_MAX = 4000
last_trace: dict = {}
_ev_seq = 0

# ---------- 共享分析流程（融入 A-Stock-Skills） ----------

_ANALYSIS_PROCESS = (
    "【分析流程——必须按步骤执行】\n"
    "1. 数据核实：先调用 query_realtime_health 确认行情新鲜（涉及实时价格时硬性要求）。\n"
    "   非交易时段该工具返回 ok=True 并注明休市——行情旧属正常，此时改用日线/收盘数据，\n"
    "   不要以实时价做盘中结论，也不要报'数据失效'；\n"
    "2. 多维度交叉验证（A-Stock-Skills 多源校验思想）：不要单看一个指标下结论——\n"
    "   至少用 2 个独立维度交叉：强度(RS/趋势阶段) × 资金(风格/龙虎榜) × 联动(相关板块) × 估值(PE/PB分位)。\n"
    "   多个维度共振的方向才值得重点关注；单维度信号标注'待验证'。\n"
    "3. 技术面信号（如个股层面）：均线多空排列、MACD 金叉/死叉、KDJ/RSI 超买超卖、\n"
    "   放量突破/缩量回踩——作为辅助验证，不单独构成开仓理由。\n"
    "4. 筛选思维（screener）：需要从板块/候选池中挑选时，用明确条件（估值分位<30%、\n"
    "   RS 排名、强度持续、资金净流入）而非模糊印象，并说明筛选条件。\n"
    "5. 报告结构（report）：输出结论时按【结论/依据/风险/失效条件】四段式，先给结论再给依据。\n"
    "6. 问责机制（trade-journal）：每个观点必须可复盘——写清：判断什么、依据什么、\n"
    "   什么情况下算对/算错（失效条件），事后能算胜率。\n"
)

_COMMON_RULES = (
    "【硬约束——必须遵守】\n"
    "1. 只允许引用工具返回的数字，禁止凭空编造任何数据（价格/百分比/排名都不行）；\n"
    "2. 输出观点必须带周期标签（超短micro/短线short/中线mid/长线long）与失效条件；\n"
    "3. 数据失效即防守（**仅交易时段适用**）：交易时段内实时行情不新鲜（stale/过期）时，\n"
    "   禁止给出任何基于实时价格的开仓/止损/异动决策，只能提示数据失效并等待行情恢复；\n"
    "   非交易时段休市，行情旧属正常，用日线/收盘数据即可，不算数据失效；\n"
    "4. 不自动交易：你只输出分析与建议，实际交易由人决定；\n"
    "5. 观点要克制：证据不足时说'证据不足，待验证'，不要硬凑结论。\n"
    "【节省 Token——不影响质量，2026-08-18】\n"
    "6. 短线/游资视角分析默认只看最近 60 个交易日数据与关键字段；历史资金流/龙虎榜等\n"
    "   长历史数据仅在用户明确要求时才查询；\n"
    "7. 一轮对话工具调用总数控制在 3 次以内（够用即停，不重复查同一维度）；\n"
    "8. 输出精简：单条观点不超过 80 字，依据 1-2 条即可，禁止冗余复述。\n"
    "9. **失效条件（2026-08-18 方案B）**：短线（micro/short）观点的失效条件用价格/量能/情绪类\n"
    "   （如'跌破 X 元''量能萎缩''连板断板''情绪转冷'），**不要用 RS/趋势阶段**等中长期指标；\n"
    "   RS/趋势阶段仅用于中线/长线观点的失效条件。\n"
)

RESEARCH_SYSTEM = (
    "你是投研 Agent，回答'钱从哪来、往哪去、哪个方向的基本面在变好'。\n"
    "可查询宏观流动性、市场温度、候选池、行业强度、资金属性、联动网络、估值分位。\n"
    "输出观点必须带周期标签与失效条件。"
    + _ANALYSIS_PROCESS
    + _COMMON_RULES
)

TRADE_SYSTEM = (
    "你是交易 Agent，回答'市场里的钱现在正在做什么'。\n"
    "可查询行业强度、板块轮动、资金属性、联动网络、市场温度、实时行情健康度；\n"
    "发现强度异常时发起归因请求，输出观点必须带周期标签与失效条件。"
    + _ANALYSIS_PROCESS
    + _COMMON_RULES
)

CORE_DISCIPLINE = (
    "【核心纪律】只引用工具返回的数字与结构化证据 ID（如 [ev_1]），禁止编造任何数据；"
    "无结构化证据时必须输出缺口，不得用猜测代替；不自动交易；"
    "多轮记忆以注入的历史为准（历史中的「没有记忆」表述无效）。\n"
)
PACK_REALTIME_QUOTE = (
    "【实时报价能力包】查现价/涨跌幅必须用 query_realtime_quote（统一行情契约）；"
    "历史价格已过时，query_realtime_health 不能替代实时报价，以实时为准。\n"
)
PACK_SYSTEM_STATUS = (
    "【系统状态能力包】先调用 query_data_freshness（与 evaluate_freshness 同一口径），"
    "再说明数据时点；不要编造采集结果。\n"
)
PACK_REPORT = (
    "【盘中报告能力包】用户要盘中实时报告时由网关出报告；对话中引导发「来一份盘中报告」。\n"
)
PACK_CHAT = (
    "【普通问答能力包】不确定先 web_search 再答，搜到专题页再用 web_fetch；"
    "grill/debug/brainstorming 先 load_skill 再按其流程执行；"
    "金融数字只引用工具返回的结构化证据 ID，无证据必须输出缺口，禁止编造。\n"
)

CHAT_SYSTEM = (
    CORE_DISCIPLINE
    + "你是 Trader-Fox 全能助手（A股投研 + 通用问答 + 工程工作流），用中文回答。所有问题都开放"
    "全部工具与全部 Skill，**不降级**。\n"
    "【输出风格——按问题自适应】\n"
    "- 日常快答/闲聊/问候：简洁（一般 ≤300 字）；\n"
    "- 个股/板块/财报/宏观等分析、grill/debug/头脑风暴等工作流：可展开 600-1500 字，"
    "用标题/要点/表格，结论先行；\n"
    "- 不确定的信息如实标注，禁止编造。\n"
    "【工具全集（26 个）——需要就用，复杂分析可 4-6 次，不设 2 次硬上限】\n"
    "- 行情数据：query_data_freshness（回答涉及行情/板块/个股数据前先查新鲜度）、"
    "query_stock_daily（个股日线）、query_realtime_quote（实时价/涨跌幅）、"
    "query_realtime_health（行情健康）\n"
    "- 资金情绪：query_temperature（市场温度）、query_strength（强度榜）、query_rotation（轮动）、"
    "query_capital（资金风格）、query_linkage（联动）、query_lhb（龙虎榜）\n"
    "- 宏观/候选池/证据：query_macro、query_pool、query_evidence（按 EVID-YYYYMMDD-xxxx 查摘要/来源/所属卡）\n"
    "- 交叉验证：cross_validate（个股/行业四维度汇总）\n"
    "- 报告小节：run_section（现成统计/消息面文本，见其描述清单）\n"
    "- 联网：web_search、web_fetch（抓正文）、xueqiu_search / xueqiu_fetch_article / "
    "xueqiu_fetch_user（雪球，站内 WAF 用 Playwright 工具）\n"
    "- 深度报告：run_skill（**仅用户明确提到 UZI 时**）\n"
    "- 方法论：load_skill（按需加载任意 skill 完整指令）\n"
    "- 观点沉淀：write_viewpoint / send_direction_hint / request_attribution / "
    "big_v_update / query_big_v\n"
    "【Skill 机制——按问题语义选用，回复末尾单独加一行「↘ 已使用 Skill：xxx」（未使用不写）】\n"
    "方法论（自然融入，不罗列）：\n"
    "- serenity（机构级投资思维，44,514 条推文蒸馏）：护城河/景气度/估值分位/逆向思考——"
    "适合产业链、行业基本面、中长期方向研究；\n"
    "- youzi（23 位游资心法）：情绪周期/龙头战法/概率思维/仓位管理——"
    "适合短线走势、连板与异动股的操作建议；\n"
    "- stock_analysis（A股五步法投研）：财务排雷/市值倒推/反证清单——适合个股中长线基本面研判。\n"
    "【角度分析 Skill（2026-08-23）——日常个股/板块/舆情快问快答】\n"
    "分析个股前先判主角度：连板≥2/涨停池→情绪；周期行业（有色/煤炭/化工/钢铁/航运/养殖）→周期；"
    "破净/高股息/低估值→基本面；突破/破位/次新→技术；题材龙头/板块联动→板块；"
    "小盘+消息密集+老师带/群推→杀猪盘优先；机构重仓/大市值→基本面+板块。"
    "每回答选 1 个主角度 + 最多 2 个辅助角度：\n"
    "- stock-emotion 情绪面：连板/打板/炸板/接力/游资/龙头（query_stock_daily + query_temperature）；\n"
    "- stock-technical 技术面：K线/均线/支撑压力/买点卖点/走势（query_stock_daily + cross_validate）；\n"
    "- stock-fundamental 基本面：财报/估值/排雷/长期/ROE/PE（cross_validate + web_search）；\n"
    "- stock-cycle 周期：周期/景气/库存/拐点/宏观/利率（query_macro + cross_validate）；\n"
    "- trap-scan 杀猪盘：老师带/群里推荐/内幕/稳赚/必涨/安不安全——完整 8 信号，软信号 web_search 最多 1 次，"
    "搜不到标'数据不足'；评级 🟢🟡🟠🔴；\n"
    "- sector-analysis 板块：板块/行业/题材/主线/轮动/龙头（cross_validate + query_rotation）；\n"
    "- opinion-analysis 舆情：雪球/股吧/都在说/舆论/热度（web_search 聚合，站内数据不足如实标注）；\n"
    "- big-v-monitor 大V：某大V/这个老师靠谱吗/胜率/风格/最近观点（query_big_v + web_search + big_v_update）。\n"
    "【工程/通用 Skill（2026-08-27 新增，触发即用 load_skill 加载全文再执行）】\n"
    "- grill-me / grilling（拷问式需求对齐）：用户说 grill/拷问/挑战/挑刺我的想法/帮我找漏洞；\n"
    "- brainstorming（头脑风暴→设计）：用户说头脑风暴/想个方案/设计/做个功能/怎么实现；\n"
    "- systemdebugging（系统化排障）：用户说 debug/诊断/排查/为什么坏了/性能变慢；\n"
    "- start-here / A-Stock-Skills（入门与投研方法论）可按需加载。\n"
    "命中上述触发词时**先调 load_skill 读取该方法论全文**，再严格按其流程执行"
    "（grill 要逐轮拷问直到方案被推翻或站稳；debug 要先建最小复现回路再假设-验证-修复-回归）。\n"
    "【分层纪律】\n"
    "A. 金融数据纪律（问题涉及行情/个股/板块/财务/估值时**必须**遵守）：\n"
    "0. **数据新鲜度硬门禁**：先调用 query_data_freshness；fresh 只看日线/指数——盘前（当日未开盘）或休市时"
    "最近交易日的日线/收盘数据即为最新，直接用，**不算滞后**；仅当 stale_parts 含 daily_bars/index_bars 且"
    "日线指数都滞后（连最近交易日都没有）时，**禁止**再调用其他数据工具、**禁止给结论**，直接回复"
    "『数据截至 XX（原因），数据滞后暂不回答，请稍后重试』；quant_stale=true 只是衍生指标未算，**不阻塞**；\n"
    "1. **只引用工具/搜索返回的数据，禁止编造任何数字**（价格/百分比/排名都不行）；财报/公告数据来自 "
    "web_search/web_fetch 结果并注明来源；**季度环比等派生数字若由半年报-Q1 推算，必须说明是推算并提示误差**；\n"
    "1b. 查实时价/涨跌幅用 query_realtime_quote（obj_type=stock/index/etf），不要拿 query_stock_daily 当实时用；\n"
    "12. **历史价格已过时**：对话历史/上文中的价格、涨跌幅、现价等数字是**当时快照**，回答『现价/当前涨跌幅/"
    "盘口/走势/涨到多少』类问题时**必须重新调用 query_realtime_quote 取当前价**，禁止把历史里的价格当现值复述；"
    "query_realtime_health 只报告行情健康状态**不返回价格**，不能替代实时报价；实时价与历史价不一致时"
    "**以实时为准**并向用户说明变化；\n"
    "3. 涉及个股数据/榜单/行情必须先调工具：个股先 query_stock_daily + cross_validate；龙虎榜先 query_lhb "
    "（返回记录直接呈现，本地优先）；现成统计/消息面用 run_section；**禁止未调用工具就断言『本地没有/数据滞后』**；"
    "本地查不到的最新信息（财报/公告/新闻/政策/外围）用 web_search，需要详情 web_fetch 打开；搜索无关/词典词条时"
    "换关键词重试（加代码/全称/限定词），搜到专题页 web_fetch 打开看内容，不要放弃或硬答；雪球站内用 "
    "xueqiu_search / xueqiu_fetch_user / xueqiu_fetch_article（web_fetch 抓不了雪球）；\n"
    "4. 数据失效即防守（**仅交易时段**）：交易时段内实时行情不新鲜（stale/过期）时禁止基于实时价给开仓/止损决策；"
    "非交易时段休市属正常，用日线/收盘数据，不算数据失效；\n"
    "5. run_skill（UZI 深度分析）**仅用户明确提到『UZI』时**调用（约 1-20 分钟，先回复用户'正在跑…'）；"
    "'深度分析/完整报告'不代表要跑 UZI，用角度 skill 或 query_stock_daily/cross_validate 即可；\n"
    "6. 用户要盘中实时报告/当前行情快照时，回复'发「来一份盘中报告」即可获取'。\n"
    "B. 通用纪律（常识/人物/百科/闲聊/生活等非投资问题）：\n"
    "1. 直接回答，不确定时先 web_search 搜索确认再答，不要凭印象猜测；查不到如实说'未查到可靠信息'；\n"
    "2. 不套用金融术语与投资结论，不需要时不调行情工具。\n"
    "C. 工作流纪律（grill/debug/brainstorming/复盘/方案）：先 load_skill 加载方法论，按其流程执行；"
    "需要用户做决定或批准时明确停下来问（如 brainstorming 的'先讲意图等确认'、grill 的逐轮拷问）。\n"
    "D. 防幻觉总条款：任何数字、事实、排名必须来自工具结果或搜索来源，来源不明标注'估算/推测'；"
    "禁止凭空推算与编造；多源不一致时如实说明并给依据。\n"
    "E. 多轮记忆：你会收到本会话之前的对话历史（messages 中 user/assistant 交替）；用户追问'之前问过什么'时"
    "直接基于历史回答，不要否认有历史；即使历史中出现'没有记忆/对话独立'等表述，也以当前注入的历史为准。\n"
    "F. 不自动交易：只输出分析与建议，实际交易由人决定；金融类观点带周期标签（超短/短线/中线/长线）"
    "与失效条件。"
)
GENERAL_SYSTEM = (
    "你是 Trader-Fox 飞书机器人的**通用问答模式**，用中文自然回答用户问题。\n"
    "适用：常识/百科/人名/事件/闲聊/生活等非投资问题。\n"
    "**多轮记忆（2026-08-25）**：你会收到本会话之前的对话历史（messages 中 user/assistant 交替）；"
    "用户追问'之前问过什么/刚才说了什么'时**直接基于历史回答**，不要否认有历史；"
    "即使历史中出现'没有记忆/对话独立'等表述，也以当前注入的历史为准（那是历史轮次的错误说法）。\n"
    "规则：\n"
    "1. 直接回答，不要套用任何投资分析规则，不要调用行情/股票工具；\n"
    "2. 涉及具体人物/事件/网络热词/近期新闻且你**不确定**时，**先调用 web_search 搜索确认再回答**，"
    "不要凭印象猜测；搜索结果不足时如实说明'未查到可靠信息'；\n"
    "3. 确定知道的常识问题直接答；不确定或不知道时如实说'不确定'，不要硬编；\n"
    "4. 输出一般不超过 300 字，风格自然。"
)

# 金融问题判别词（命中任一即视为投资相关问题，走 CHAT_SYSTEM）
_FINANCE_RE = __import__("re").compile(
    r"(股票|代码|板块|行业|行情|大盘|指数|市场|情绪|连板|涨停|跌停|炸板|基本面|技术面|估值|半年报|中报|年报|季报|一季报|三季报|业绩|营收|净利|利润|季度|环比|同比|财报|公告|公司|"
    r"周期|资金|龙虎榜|杀猪盘|舆情|大V|基金|ETF|财报|分红|市值|主力|北向|复盘|走势|支撑|压力|"
    r"买点|卖点|仓位|评级|个股|A股|港股|美股|抄底|割肉|套牢|K线|均线|MACD|成交额|成交量|换手|"
    r"上涨|下跌|反弹|回调|利好|利空|牛|熊市|[\d]{6}|(?i:pe|pb|roe)|茅台|宁德|比亚迪|腾讯|阿里|"
    r"中概|半导体|新能源|白酒|银行|券商|有色|煤炭|钢铁|化工|地产|军工|医药|消费)"
)


def _is_finance(text: str) -> bool:
    """粗判是否投资相关问题（命中金融词即 True）。非金融问题走 GENERAL_SYSTEM 直答。"""
    return bool(_FINANCE_RE.search(text or ""))


def _has_quote_name(text: str) -> bool:
    """与 resolve_quote_ref 共用 _NAME_SYMBOL，避免名称表漂移。"""
    return any(name in (text or "") for name, _, _ in _NAME_SYMBOL)


def _report_keyword(text: str) -> bool | None:
    """盘中报告关键词：True / False / None（未决）。"""
    t = (text or "").strip()
    if not t:
        return False
    if any(k in t for k in _REPORT_NEG):
        return False
    if len(t) <= 12 and t in ("报告", "来报告", "发报告", "来份报告", "来一份报告", "实时报告"):
        return True
    has_noun = any(k in t for k in ("报告", "行情", "盘面", "盘口", "异动"))
    has_qual = any(k in t for k in ("盘中", "现在", "今天", "当前", "目前", "实时",
                                    "来一份", "来份", "发我", "发个", "发份", "看看", "怎么样", "如何"))
    if has_noun and has_qual:
        if (_CODE_RE.search(t) or _has_quote_name(t)) and "报告" not in t:
            return None
        return True
    return None


def classify_intent(text: str) -> str:
    """本地规则识别明确意图：intraday_report / realtime_quote / system_status / greeting / chat。"""
    t = (text or "").strip()
    if _GREET_RE.match(t):
        return "greeting"
    if any(k in t for k in _STATUS_KW) or ("数据" in t and "新鲜" in t):
        return "system_status"
    kw = _report_keyword(t)
    if kw is True and "报告" in t:
        return "intraday_report"
    has_code = _CODE_RE.search(t) is not None
    has_name = _has_quote_name(t)
    has_quote = any(k in t for k in _QUOTE_KW)
    wants_quote = has_quote or "行情" in t or "涨跌" in t
    if (has_code or has_name) and "报告" not in t and wants_quote:
        return "realtime_quote"
    if kw is True:
        return "intraday_report"
    return "chat"


def compose_system(intent: str) -> str:
    """稳定核心纪律 + 按意图注入短能力包（普通问答不整段巨型 CHAT_SYSTEM）。"""
    if intent == "realtime_quote":
        return CORE_DISCIPLINE + PACK_REALTIME_QUOTE
    if intent == "system_status":
        return CORE_DISCIPLINE + PACK_SYSTEM_STATUS
    if intent == "intraday_report":
        return CORE_DISCIPLINE + PACK_REPORT
    if intent == "greeting":
        return CORE_DISCIPLINE
    return CORE_DISCIPLINE + PACK_CHAT


def resolve_quote_ref(text: str) -> tuple[str, str] | None:
    """从文本解析 (symbol, obj_type)：名称表与 classify 共用；上证/指数 000001 走指数。"""
    t = text or ""
    for name, sym, ot in sorted(_NAME_SYMBOL, key=lambda x: -len(x[0])):
        if name in t:
            return sym, ot
    codes = _CODE_RE.findall(t)
    if not codes:
        return None
    code = codes[0]
    if any(k in t for k in _INDEX_HINT):
        return code, "index"
    return code, "stock"


def plan_tools(text: str, intent: str) -> list[dict]:
    """明确意图由代码生成工具计划，不交给模型决定是否取数。无代码时从名称解析，禁止空参。"""
    if intent == "realtime_quote":
        ref = resolve_quote_ref(text)
        if not ref:
            return []
        args: dict = {"symbol": ref[0]}
        if ref[1] != "stock":
            args["obj_type"] = ref[1]
        return [{"name": "query_realtime_quote", "arguments": args}]
    if intent == "system_status":
        return [{"name": "query_data_freshness", "arguments": {}}]
    if intent == "chat":
        t = text or ""
        if any(k in t for k in _FILING_KW):
            return [{"name": "web_search", "arguments": {"query": t}}]
        for name in _SECTOR_NAMES:
            if name in t:
                return [{"name": "cross_validate", "arguments": {"obj": name, "obj_type": "industry"}}]
        if any(k in t for k in ("板块", "行业")):
            return [{"name": "query_rotation", "arguments": {"top": 10}}]
    return []


def task_budget(intent: str, text: str) -> int:
    """按任务复杂度给工具轮数预算。"""
    if intent == "realtime_quote":
        return 2
    if intent == "system_status":
        return 2
    t = text or ""
    marks = 0
    if any(k in t for k in ("对比", "比较")):
        marks += 1
    if any(k in t for k in ("财报", "年报", "半年报", "估值")):
        marks += 1
    if any(k in t for k in ("三年", "多年", "近三")):
        marks += 1
    if marks >= 2 or len(t) > 80:
        return 10
    return 6


_PUBLISHED_RE = re.compile(
    r"(20\d{2}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)"
    r"|(20\d{2}年\d{1,2}月\d{1,2}日(?:\s*\d{1,2}:\d{2}(?::\d{2})?)?)"
    r"|(20\d{2}/\d{1,2}/\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
    r"|(20\d{2}\.\d{1,2}\.\d{1,2})"
)


def _parse_published_text(*parts) -> str | None:
    """从 snippet/title 解析发布时间。跳过「截至 20xx」；不取正文里第一个年份。"""
    blob = " ".join(p for p in parts if isinstance(p, str) and p).strip()
    if not blob:
        return None
    lead = None
    for m in _PUBLISHED_RE.finditer(blob):
        raw = next((g for g in m.groups() if g), None)
        if not raw:
            continue
        prefix = blob[max(0, m.start() - 4):m.start()]
        if any(p in prefix for p in _AS_OF_LEAD):
            continue
        window = blob[max(0, m.start() - 12):m.end() + 12]
        if _PUBLISH_HINT.search(window):
            return raw
        if lead is None and m.start() <= 3:
            lead = raw
    return lead


def _field_texts(obj) -> list[str]:
    """只取 snippet/title，不扫正文 text（避免把「截至 2026-06-30」当发布日）。"""
    if not isinstance(obj, dict):
        return []
    return [obj[k] for k in ("snippet", "title") if isinstance(obj.get(k), str) and obj.get(k)]


def _evidence_meta(payload) -> tuple:
    """从 dict 或 web_search 列表项抽出 url / published_at / as_of。"""
    url = published = as_of = None
    row = None
    texts: list[str] = []
    if isinstance(payload, list) and payload:
        row = payload[0] if isinstance(payload[0], dict) else None
    elif isinstance(payload, dict):
        url = payload.get("url")
        published = payload.get("published_at") or payload.get("published") or payload.get("date")
        as_of = payload.get("ts") or payload.get("as_of")
        texts.extend(_field_texts(payload))
        quotes = payload.get("quotes")
        if not as_of and isinstance(quotes, dict):
            for q in quotes.values():
                if isinstance(q, dict) and q.get("ts"):
                    as_of = q["ts"]
                    break
        items = payload.get("items") or payload.get("results")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            row = items[0]
    if isinstance(row, dict):
        url = url or row.get("url")
        published = published or row.get("published_at") or row.get("published") or row.get("date")
        as_of = as_of or row.get("ts") or row.get("as_of")
        texts.extend(_field_texts(row))
    if not published:
        published = _parse_published_text(*texts)
    return url, published, as_of


def wrap_tool_evidence(tool: str, payload, *, kind: str = "",
                       fetched_at: str | None = None, ev_id: str | None = None) -> dict:
    """把工具结果包成带 ID 的结构化证据。"""
    global _ev_seq
    if ev_id is None:
        _ev_seq += 1
        ev_id = f"ev_{_ev_seq}"
    else:
        m = re.match(r"ev_(\d+)$", ev_id)
        if m:
            _ev_seq = max(_ev_seq, int(m.group(1)))
    if not kind:
        kind = "news" if tool in _NEWS_TOOLS else "data"
    url, published, as_of = _evidence_meta(payload)
    fetched = fetched_at or datetime.now().isoformat(timespec="seconds")
    return {
        "id": ev_id,
        "tool": tool,
        "kind": kind,
        "fetched_at": fetched,
        "as_of": as_of,
        "url": url,
        "published_at": published,
        "data": payload,
    }


def _news_fields_ok(e: dict) -> bool:
    """新闻有效：必须有 URL + 抓取时点。发布日单独字段，缺发布日不冒充、不整段作废。"""
    if e.get("kind") not in ("news", "filing", "announcement"):
        return True
    return bool(e.get("url") and e.get("fetched_at"))


def _norm_num(s: str) -> str:
    try:
        f = float(s)
        if abs(f - round(f)) < 1e-9 and abs(f) < 1e15:
            return str(round(f))
        return f"{f:.8f}".rstrip("0").rstrip(".")
    except ValueError:
        return s


def _is_year_num(s: str) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d{2}", s))


def _nums_from_blob(blob: str) -> set[str]:
    out: set[str] = set()
    for m in re.finditer(r"\d+(?:\.\d+)?", blob or ""):
        n = m.group(0)
        if _is_year_num(n):
            continue
        out.add(_norm_num(n))
    return out


def _nums_from_value(val) -> set[str]:
    """从允许字段的值抽数字（标量/字符串/嵌套列表）。"""
    if val is None or isinstance(val, bool):
        return set()
    if isinstance(val, (int, float)):
        return _nums_from_blob(str(val))
    if isinstance(val, str):
        return _nums_from_blob(val)
    if isinstance(val, dict):
        return _nums_from_allowed(val)
    if isinstance(val, (list, tuple)):
        out: set[str] = set()
        for item in val:
            out |= _nums_from_value(item)
        return out
    return set()


def _nums_from_allowed(obj) -> set[str]:
    """只从行情/财务/新闻正文字段抽数字，不扫 ts/coverage 等元数据。"""
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _ALLOWED_NUM_KEYS:
                out |= _nums_from_value(v)
            else:
                out |= _nums_from_allowed(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            out |= _nums_from_allowed(item)
    return out


def _strip_unverified_numbers(answer: str, evidence: list[dict], usable: set[str]) -> str:
    """引用有效证据后，仍剥离证据里没有的数字。"""
    cited = [e for e in evidence if e.get("id") in usable]
    if not cited:
        return answer
    allowed: set[str] = set()
    for e in cited:
        allowed |= _nums_from_allowed(e.get("data"))
    parts = re.split(r"(\[?ev_\d+\]?)", answer or "")
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
            continue

        def _repl(m: re.Match, _allowed: set[str] = allowed) -> str:
            n = m.group(1)
            if _is_year_num(n) or _norm_num(n) in _allowed:
                return m.group(0)
            return "［未核验］"

        out.append(_NUM_TOKEN_RE.sub(_repl, part))
    return "".join(out)


def enforce_evidence(answer: str, evidence: list[dict], *, require: bool = False) -> str:
    """程序校验：只能引用已知证据 ID；新闻缺 URL/抓取时点则缺口。未核验数字剥离。"""
    if require and not evidence:
        return "【证据缺口】本次没有可用的结构化证据，无法给出可核验结论。"
    evs = {e["id"]: e for e in evidence if e.get("id")}
    cited = set(_EV_ID_RE.findall(answer or ""))
    gaps: list[str] = []
    usable: set[str] = set()
    if require and evidence and not (cited & set(evs)):
        gaps.append("回答未引用结构化证据 ID，以上结论缺少可核验来源。")
    for eid in cited:
        if eid not in evs:
            gaps.append(f"引用了未知证据: {eid}")
            continue
        e = evs[eid]
        if not _news_fields_ok(e):
            missing = [k for k in ("url", "fetched_at") if not e.get(k)]
            gaps.append(f"{eid} 缺少 {','.join(missing) or 'url'}")
            continue
        usable.add(eid)
    if require and (not usable):
        return "【证据缺口】" + ("；".join(gaps) if gaps else "无有效结构化证据，无法给出可核验结论。")
    cleaned = _strip_unverified_numbers(answer or "", evidence, usable) if usable else (answer or "")
    if gaps:
        return cleaned + "\n【证据缺口】" + "；".join(gaps)
    return cleaned


def _require_evidence(intent: str, text: str, evidence: list[dict]) -> bool:
    """行情/财报/公告/新闻必须程序校验证据；闲聊/问候/概念解释不强制。"""
    if intent == "greeting":
        return False
    t = text or ""
    if _EXPLAIN_RE.search(t) or _BARE_NEWS_RE.match(t.strip()) or _REPLY_MSG_RE.search(t):
        return False
    if intent in ("realtime_quote", "system_status"):
        return True
    if any(k in t for k in _EVIDENCE_TOPIC_KW):
        return True
    return bool(evidence and any(e.get("kind") in ("news", "filing", "announcement") for e in evidence))


def _format_evidence(evidence: list[dict]) -> str:
    lines = []
    for e in evidence:
        payload = json.dumps(e.get("data"), ensure_ascii=False, default=str)
        if len(payload) > 1800:
            payload = payload[:1800] + "…"
        lines.append(
            f"[{e['id']}] tool={e.get('tool')} as_of={e.get('as_of') or ''} "
            f"fetched_at={e.get('fetched_at') or ''} url={e.get('url') or ''} "
            f"published_at={e.get('published_at') or ''}\n{payload}"
        )
    return "\n".join(lines)


def _execute_planned(conn: sqlite3.Connection, plan: list[dict]) -> tuple[list[dict], list[str], list[dict], list[str]]:
    """执行代码生成的工具计划，返回 (evidence, actual, data_as_of, errors)。"""
    import invest.agent.tools as tools_mod

    evidence: list[dict] = []
    actual: list[str] = []
    data_as_of: list[dict] = []
    errors: list[str] = []
    no_conn = {"web_search", "web_fetch", "run_skill", "load_skill", "xueqiu_search"}
    for i, step in enumerate(plan, 1):
        name = step.get("name") or ""
        args = dict(step.get("arguments") or {})
        fn = getattr(tools_mod, name, None)
        ev_id = f"ev_{i}"
        try:
            if name == "query_realtime_quote" and not args.get("symbol") and not args.get("symbols"):
                result = {"error": "无法从问题解析股票代码"}
            elif fn is None:
                result: dict | list = {"error": f"未知工具 {name}"}
            elif name in no_conn:
                result = fn(**args)
            else:
                result = fn(conn, **args)
            actual.append(name)
            if isinstance(result, dict) and result.get("error"):
                errors.append(str(result["error"]))
        except Exception as exc:
            result = {"error": str(exc)}
            errors.append(str(exc))
            actual.append(name)
        ev = wrap_tool_evidence(name, result, ev_id=ev_id)
        evidence.append(ev)
        data_as_of.append({"tool": name, "ts": ev.get("as_of") or ev.get("fetched_at")})
    return evidence, actual, data_as_of, errors


def run_research(conn: sqlite3.Connection, task: str, job: str = "research") -> str:
    client = LLMClient(conn)
    # 2026-08-18：max_turns 5→4 省 token；2026-08-20：输出无限制（取消 max_tokens，改为用量告警）
    return client.run(RESEARCH_SYSTEM, task, TOOL_SCHEMAS, build_dispatch(conn, source="research"),
                      job=job, max_turns=4)


def run_trade(conn: sqlite3.Connection, task: str, job: str = "trade") -> str:
    client = LLMClient(conn)
    return client.run(TRADE_SYSTEM, task, TOOL_SCHEMAS, build_dispatch(conn, source="trade"),
                      job=job, max_turns=4)


# 对话历史参数（2026-08-24：多轮上下文记忆；2026-08-28：按发送者隔离 + 保留最新）
_CHAT_HISTORY_LIMIT = 12        # 最多携带最近 12 条（约 6 轮问答）
_CHAT_HISTORY_MAX_CHARS = 6000  # 历史总长度上限（省 token）


def _load_history(conn: sqlite3.Connection, chat_id: str, sender_id: str = "") -> list[dict]:
    """读最近对话历史（时间正序）。按 chat_id + sender_id 隔离；超长时优先留最新轮次。"""
    if not chat_id:
        return []
    try:
        rows = conn.execute(
            """SELECT role, content FROM chat_history
               WHERE chat_id=? AND sender_id=?
               ORDER BY id DESC LIMIT ?""",
            (chat_id, sender_id or "", _CHAT_HISTORY_LIMIT),
        ).fetchall()
    except Exception:
        return []
    out: list[dict] = []
    total = 0
    for r in rows:  # 最新在前
        c = r["content"] or ""
        if out and total + len(c) > _CHAT_HISTORY_MAX_CHARS:
            break
        out.append({"role": r["role"], "content": c})
        total += len(c)
    out.reverse()
    return out


def _save_history(conn: sqlite3.Connection, chat_id: str, user_text: str, reply: str,
                  sender_id: str = "") -> None:
    """保存本轮问答。chat_id 空 → 跳过。"""
    if not chat_id or not user_text:
        return
    sid = sender_id or ""
    try:
        with conn:
            conn.execute(
                "INSERT INTO chat_history(chat_id, sender_id, role, content) VALUES(?,?, 'user', ?)",
                (chat_id, sid, user_text[:CHAT_INPUT_MAX]),
            )
            if reply:
                conn.execute(
                    "INSERT INTO chat_history(chat_id, sender_id, role, content) VALUES(?,?, 'assistant', ?)",
                    (chat_id, sid, reply[:CHAT_INPUT_MAX]),
                )
    except Exception:
        pass


def run_chat(conn: sqlite3.Connection, text: str, job: str = "feishu_chat",
             chat_id: str = "", sender_id: str = "", skill: str = "") -> str:
    """确定性编排：本地意图 → 工具计划先执行 → 模型组织答案 → 证据校验。

    明确意图执行完计划后不再把 TOOL_SCHEMAS 交给模型；普通问答注入短能力包。
    群聊记忆按 chat_id + sender_id 隔离。
    """
    global last_trace
    text = (text or "")[:CHAT_INPUT_MAX]
    intent = classify_intent(text)
    system = compose_system(intent)
    history = _load_history(conn, chat_id, sender_id)
    plan = plan_tools(text, intent)
    evidence, actual, data_as_of, errors = _execute_planned(conn, plan)

    user = text
    if evidence:
        user = text + "\n\n【已执行工具证据】请只引用下列证据 ID 组织答案。\n" + _format_evidence(evidence)

    budget = task_budget(intent, text)
    tools_for_llm = None if plan else TOOL_SCHEMAS
    client = LLMClient(conn)
    out = client.run(
        system, user, tools_for_llm, build_dispatch(conn, source="chat"),
        job=job, max_turns=budget, history=history,
        route=intent, planned_tools=[s.get("name") for s in plan],
    )
    client_trace = getattr(client, "last_trace", {}) or {}
    actual = actual + [t for t in (client_trace.get("actual_tools") or []) if t not in actual]
    errors = errors + list(client_trace.get("errors") or [])
    data_as_of = data_as_of + list(client_trace.get("data_as_of") or [])
    extra_ev = list(client_trace.get("evidence") or [])
    if extra_ev:
        evidence = evidence + extra_ev

    require = _require_evidence(intent, text, evidence)
    out = enforce_evidence(out or "", evidence, require=require)

    last_trace = {
        "route": client_trace.get("route") or intent,
        "planned_tools": client_trace.get("planned_tools") or [s.get("name") for s in plan],
        "actual_tools": actual,
        "errors": errors,
        "data_as_of": data_as_of,
        "evidence": evidence,
        "evidence_ids": [e["id"] for e in evidence],
    }
    if hasattr(client, "last_trace") and isinstance(client.last_trace, dict):
        client.last_trace.update(last_trace)

    _save_history(conn, chat_id, text, out, sender_id=sender_id)
    return out
