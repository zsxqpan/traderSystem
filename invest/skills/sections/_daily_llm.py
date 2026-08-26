"""盘后日报 LLM 环节（2026-08-22，a3_daily 用，job='daily_report'）。

4 次调用：
1. intraday_review_llm：盘中观点复盘（点2）——当日 source='intraday_report' 观点 vs 当日实际；
2. board_analysis_llm：重要板块总分析（点3）——固定方向清单（AI硬件/AI软件/机器人/金融/
   金属/新旧能源/内需）+ 各方向 ETF（纯度高于板块指数）+ 异动个股；
3. plan_gen_llm：明日预案（点4）——推荐介入股票 + 关注/持仓股操作预案；
4. plan_review_llm：预案质量复盘（点4）——最近 N 日 source='plan' 预案 vs 实际。

失败/解析失败返回 {}，调用方回退（省略该节或直列数据），不阻断报告。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是 A 股盘后策略分析师（机构级：护城河/景气度/估值分位 + 游资：情绪周期/龙头战法）。\n"
    "只许基于给定数据推理，禁止编造数字。输出严格 JSON，不要任何其他文字。"
)


def _llm(conn, system: str, user: str, max_tokens: int) -> str:
    from invest.agent.llm import LLMClient

    client = LLMClient(conn=conn)
    return client.run(system=system, user=user, job="daily_report",
                      max_turns=1, max_tokens=max_tokens)


def _parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t.removeprefix("json")
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        d = json.loads(t[start:end + 1])
        return d if isinstance(d, dict) else None
    except ValueError:
        return None


def intraday_review_llm(db_path: str, ctx: dict) -> dict:
    """点2：盘中观点复盘。ctx: {views_text, actual_text}。失败返回 {}。"""
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            out = _llm(conn, _SYSTEM,
                       f"以下是今日竞价报告与盘中报告给出的观点（预测/操作建议/短线判断，来源已标注）：\n{ctx.get('views_text') or '（今日无观点）'}\n\n"
                       f"以下是当日实际表现：\n{ctx.get('actual_text') or '暂无'}\n\n"
                       "请输出 JSON：\n"
                       '{"verdict": "逐条判断观点对错（对/错/部分对；竞价预判与盘中判断分别点评，50字内）",\n'
                       '"wrong_reasons": ["错误原因（数据/逻辑/突发，每条20字内）"],\n'
                       '"lessons": ["沉淀成经验的一句话（供固化为复盘 skill，每条20字内）"]}\n'
                       "没有观点则 verdict 写'今日无观点可复盘'，数组为空。",
                       max_tokens=600)
            return _parse_json(out) or {}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("盘中观点复盘 LLM 失败: %s", exc)
        return {}


def board_analysis_llm(db_path: str, ctx: dict) -> dict:
    """点3：重要板块总分析。ctx: {etf_sector, sector_top, ladder, stock_moves}。失败返回 {}。"""
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            out = _llm(conn, _SYSTEM,
                       "以下是今日重要方向数据（ETF 纯度高于板块指数，最能体现方向变化）：\n"
                       f"各方向ETF:\n{ctx.get('etf_sector') or '暂无'}\n\n"
                       f"板块涨幅TOP:\n{ctx.get('sector_top') or '暂无'}\n"
                       f"连板梯队:\n{ctx.get('ladder') or '暂无'}\n"
                       f"异动个股:\n{ctx.get('stock_moves') or '暂无'}\n\n"
                       "请覆盖分析以下方向（AI硬件/AI软件/机器人/金融/金属/新能源/旧能源/内需），输出 JSON：\n"
                       '{"boards": [{"name": "方向名",'
                       '"active": true或false（当天是否有明显异动）,'
                       '"analysis": "active=true：驱动/内部结构/ETF验证/龙头，50字内；'
                       'active=false：一句话中线状态（如横盘待变盘/持续阴跌未见底/温和上行），25字内，避免过度分析",'
                       '"stock_move": "方向整体无变化但有个股异动时：个股名+一句话归因；无则空字符串"}]\n'
                       "注意：不活跃方向只给一句话中线状态即可。",
                       max_tokens=1800)
            return _parse_json(out) or {}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("板块总分析 LLM 失败: %s", exc)
        return {}


def plan_gen_llm(db_path: str, ctx: dict) -> dict:
    """点4：明日预案。ctx: {summary, holdings, plan_history}。失败返回 {}。"""
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            out = _llm(conn, _SYSTEM,
                       "以下是今日盘面总结：\n" + (ctx.get("summary") or "暂无") + "\n\n"
                       "以下是关注/持仓股（用户指定，多为持仓；系统推荐仅为迭代验证）：\n"
                       + (ctx.get("holdings") or "暂无") + "\n\n"
                       "以下是最近几天预案质量复盘（若有）：\n" + (ctx.get("plan_history") or "暂无") + "\n\n"
                       "请输出明日预案 JSON：\n"
                       '{"direction": "明日主线方向判断（25字内）",\n'
                       '"picks": [{"name": "明日可介入股票名", "reason": "介入理由（结合今日主线/ETF，20字内）",'
                       '"plan": "介入预案（如回踩X均线低吸/打板/半路，20字内）"}],  # 最多3只，仅系统探索推荐\n'
                       '"plans": [{"symbol": "关注/持仓股代码", "action": "明日操作预案（持有/减/加/止盈止损位，20字内）"}]}\n'
                       "plans 必须覆盖所有列出的关注/持仓股。",
                       max_tokens=1200)
            return _parse_json(out) or {}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("明日预案 LLM 失败: %s", exc)
        return {}


def plan_review_llm(db_path: str, ctx: dict) -> dict:
    """点4：预案质量复盘。ctx: {history: [{date, plan_summary, actual_summary}]}。失败返回 {}。"""
    if not ctx.get("history"):
        return {}
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            lines = []
            for h in ctx["history"][-5:]:
                lines.append(f"[{h['date']}] 预案: {h['plan_summary']}\n    实际: {h['actual_summary']}")
            out = _llm(conn, _SYSTEM,
                       "以下是最近几天的盘后预案与其后实际表现的对照：\n" + "\n".join(lines) + "\n\n"
                       "请输出 JSON：\n"
                       '{"quality": "预案质量总评（预测与盘面结果的契合度，40字内）",\n'
                       '"fixes": ["优化预案推演方式的具体建议（每条25字内，可沉淀为预案推演 skill 的迭代方向）"]}\n'
                       "若只有一天数据，quality 简短即可，fixes 给 1-2 条。",
                       max_tokens=600)
            return _parse_json(out) or {}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("预案质量复盘 LLM 失败: %s", exc)
        return {}


def etf_analysis_llm(db_path: str, ctx: dict) -> dict:
    """点1：指数 ETF 解读（2026-08-24，明显变化时详细归因 + 风格变化探讨）。

    ctx: {material: "每行一个 ETF 的 涨跌/量比/主力净/成交额"}。失败返回 {}。
    """
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            out = _llm(conn, _SYSTEM,
                       "以下是主要指数 ETF 当日数据（涨跌/量比/主力净流入/成交额）：\n"
                       + (ctx.get("material") or "暂无") + "\n\n"
                       "请输出 JSON：\n"
                       '{"summary": "整体一句话（简单概括当日 ETF 表现）",\n'
                       '"notable": "有明显变化的 ETF 及具体数字（涨跌幅/量比/超大单），无则空串",\n'
                       '"attribution": "变化归因（结合指数风格/量能/资金，2-3 句，25字内每句）",\n'
                       '"style_shift": "可能的市场风格变化探讨（大盘vs小盘/成长vs价值/板块倾向，1-2 句，无则空串）"}\n'
                       "只基于给定数据推理，禁止编造数字。",
                       max_tokens=800)
            return _parse_json(out) or {}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("ETF 解读 LLM 失败: %s", exc)
        return {}
