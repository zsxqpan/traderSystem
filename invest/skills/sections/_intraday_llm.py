"""盘中报告 LLM 环节（2026-08-22，b1_intraday 用，job='intraday_report'）。

两次调用：
1. mood_llm(db_path, ctx) -> dict：
   {mood, prediction, short_term} —— 整体情绪判断 / 盘面预测+操作建议 / 短线周期博弈判断；
2. mainline_llm(db_path, ctx) -> dict：
   {main_lines: [{direction, reason, internal, leaders[], outlook}], core_outlook}
   —— 日内主线分析（原因/内部结构/龙头/走势推演）+ 核心关注推演。

- 方法论注入：youzi（23 位游资心法：情绪周期/龙头战法/概率思维）——与 CHAT_SYSTEM 口径一致；
  后续可加 UZI deep-analysis 方法论（用户预留）。
- 失败/JSON 解析失败 → 返回空 dict，调用方回退规则输出（emotion_cycle/直列板块）；
- 模块级缓存 _TTL=120s：同一次触发内重复调用防抖。
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

_TTL = 120.0
_mood_cache: dict[str, tuple[float, dict]] = {}
_mainline_cache: dict[str, tuple[float, dict]] = {}

# 与 agents.CHAT_SYSTEM 的 youzi 描述保持一致的方法论注入
_YOUZI_SYSTEM = (
    "你是 A 股盘面分析师（youzi 游资心法：情绪周期/龙头战法/概率思维/仓位管理）。\n"
    "只许基于给出的数据推理，禁止编造任何数字。输出严格 JSON，不要任何其他文字。"
)


def _llm(conn, system: str, user: str, max_tokens: int) -> str:
    from invest.agent.llm import LLMClient

    client = LLMClient(conn=conn)
    return client.run(system=system, user=user, job="intraday_report",
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


def mood_llm(db_path: str, ctx: dict) -> dict:
    """情绪判断（调用 1）。ctx 含 temp/temp_hist/emotion/limit_up。失败返回 {}。"""
    now = time.time()
    cached = _mood_cache.get(db_path)
    if cached and now - cached[0] < _TTL:
        return cached[1]
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            prompt = (
                "以下是今日盘中情绪与连板数据：\n"
                f"- 市场温度: {ctx.get('temp_text') or '暂无'}\n"
                f"- 情绪周期(收盘口径): {ctx.get('emotion_text') or '暂无'}\n"
                f"- 今日盘中连板: {ctx.get('limit_up_text') or '暂无'}\n"
                f"- 近20日温度: {' → '.join(str(v) for v in (ctx.get('temp_hist') or [])[-20:])}\n\n"
                "请输出 JSON：\n"
                '{"mood": "整体情绪判断一句话（含温度/连板/炸板解读）",\n'
                '"prediction": "结合当天+前面多天数据的盘面预测与操作建议（如已滞涨小心回落、'
                '放量下跌不要抄底、可积极进攻等，40字内）",\n'
                '"short_term": "结合短线周期给出博弈判断（如连板率大幅提升最高板继续提高=短线主升日、'
                '龙头滞涨杂毛补涨=即将大分歧、炸板率升高=退潮防守等，40字内）"}'
            )
            out = _llm(conn, _YOUZI_SYSTEM, prompt, max_tokens=600)
            parsed = _parse_json(out)
            if parsed:
                _mood_cache[db_path] = (now, parsed)
                return parsed
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("盘中情绪 LLM 失败: %s", exc)
    return {}


def auction_llm(db_path: str, ctx: dict) -> dict:
    """竞价情绪预判（2026-08-22，a7 竞价报告用，job='auction'）。ctx 含指数/榜单/连板。失败返回 {}。"""
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            out = _llm(conn, _YOUZI_SYSTEM,
                       "以下是今日 9:25 集合竞价数据：\n"
                       f"指数竞价:\n{ctx.get('index_text') or '暂无'}\n"
                       f"竞价高开榜TOP:\n{ctx.get('gainers') or '暂无'}\n"
                       f"竞价量比榜TOP:\n{ctx.get('vol_ratio') or '暂无'}\n"
                       f"昨日连板今日竞价:\n{ctx.get('ladder') or '暂无'}\n\n"
                       "请输出竞价情绪预判 JSON：\n"
                       '{"mood": "竞价情绪强弱判断（高开家数/连板承接/量比放大，30字内）",\n'
                       '"style": "风格预判（大小盘/题材方向，25字内）",\n'
                       '"hint": "操作提示（追涨/低吸/防守，结合竞价异常，30字内）"}\n'
                       "只许基于给定数据推理，禁止编造个股与数字。",
                       max_tokens=500)
            return _parse_json(out) or {}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("竞价情绪预判 LLM 失败: %s", exc)
        return {}


def section_analysis_llm(db_path: str, ctx: dict) -> dict:
    """竞价各模块解析（2026-08-22，a7 用，job='auction'）：指数/榜单/连板/关键股/核心关注
    每模块一句解析；该模块竞价无特别消息写"无"，不强行解析。一次调用省 token。"""
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            out = _llm(conn, _YOUZI_SYSTEM,
                       "以下是今日 9:25 集合竞价各模块数据：\n"
                       f"指数竞价:\n{ctx.get('index_text') or '暂无'}\n"
                       f"高开放量榜:\n{ctx.get('boards_text') or '暂无'}\n"
                       f"昨日连板竞价:\n{ctx.get('ladder_text') or '暂无'}\n"
                       f"市场关键股票竞价:\n{ctx.get('key_text') or '暂无'}\n"
                       f"核心关注竞价:\n{ctx.get('core_text') or '暂无'}\n\n"
                       "请对每个模块给一句简要竞价解析（原因/影响，30字内；该模块竞价无特别消息写'无'，"
                       "不要强行解析），输出 JSON：\n"
                       '{"index": "指数竞价解析", "boards": "高开放量榜解析", "ladder": "连板竞价解析",'
                       '"key_stocks": "关键股票竞价解析", "core": "核心关注竞价解析"}\n'
                       "只许基于给定数据推理，禁止编造个股与数字。",
                       max_tokens=700)
            return _parse_json(out) or {}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("竞价模块解析 LLM 失败: %s", exc)
        return {}


def key_stock_llm(db_path: str, ctx: dict) -> dict:
    """关键股票竞价解析（2026-08-22，a7 竞价报告用，job='auction'）。

    输入前一日热门板块核心股票的竞价表现，每板块给一句竞价解析（原因/影响）。
    与 auction_llm（情绪预判）独立，互不影响。
    """
    if not ctx.get("blocks_text"):
        return {}
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            out = _llm(conn, _YOUZI_SYSTEM,
                       "以下是前一日热门板块核心股票今日竞价表现：\n" + ctx["blocks_text"] + "\n\n"
                       "请对每个板块给一句简要竞价解析（分析高开/低开原因与影响，"
                       "如'受消息影响高开''体现该方向资金情绪高涨''高开兑现抛压需防'，30字内），"
                       "输出 JSON：\n"
                       '{"blocks": [{"name": "板块名", "analysis": "竞价解析一句话"}]}\n'
                       "只许基于给定数据推理，禁止编造个股与数字。",
                       max_tokens=500)
            return _parse_json(out) or {}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("关键股票竞价解析 LLM 失败: %s", exc)
        return {}


def _candidate_entries(ctx: dict) -> list[dict]:
    raw = ctx.get("candidates") or []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            sym = str(item.get("symbol") or "").strip()
            name = str(item.get("name") or "").strip()
            if sym:
                out.append({"symbol": sym, "name": name})
        elif isinstance(item, str) and item.strip():
            out.append({"symbol": item.strip(), "name": ""})
    return out


def _pick_allowed(item: dict, allowed_syms: set[str]) -> bool:
    """只按规则筛出的 symbol 放行；禁止用 name 绕过。"""
    if not isinstance(item, dict):
        return False
    try:
        from invest.data.quotes import normalize_symbol

        sym = normalize_symbol(str(item.get("symbol") or ""))
    except Exception:
        sym = str(item.get("symbol") or "").strip()
    if not sym:
        return False
    return bool(allowed_syms) and sym in allowed_syms


def _schema_ok(item: dict, kind: str) -> bool:
    """picks 必须有 reason；leaders 必须有 role。"""
    if not isinstance(item, dict):
        return False
    if kind == "pick":
        return bool(str(item.get("reason") or "").strip())
    if kind == "leader":
        return bool(str(item.get("role") or "").strip())
    return True


def _norm_sym(symbol: str) -> str:
    try:
        from invest.data.quotes import normalize_symbol

        return normalize_symbol(str(symbol or ""))
    except Exception:
        return str(symbol or "").strip()


def _align_pick_name(item: dict, names_by_sym: dict[str, str]) -> dict | None:
    """放行后按候选表改写 name；候选名与自报 name 冲突时以候选表为准。"""
    if not isinstance(item, dict):
        return None
    out = dict(item)
    sym = _norm_sym(str(out.get("symbol") or ""))
    official = (names_by_sym.get(sym) or "").strip()
    if official:
        out["name"] = official
    return out


def _sanitize_mainline(parsed: dict, ctx: dict) -> dict:
    """推荐标的必须来自规则筛出的 symbol；无候选则清空 picks/leaders。"""
    if not parsed:
        return parsed
    cands = _candidate_entries(ctx)
    names_by_sym: dict[str, str] = {}
    for c in cands:
        sym = _norm_sym(c["symbol"])
        if sym:
            names_by_sym[sym] = str(c.get("name") or "").strip()
    allowed_syms = {s for s in names_by_sym}
    out = dict(parsed)
    lines = []
    for ml in out.get("main_lines") or []:
        if not isinstance(ml, dict):
            continue
        row = dict(ml)
        if not allowed_syms:
            row["picks"] = []
            row["leaders"] = []
        else:
            aligned: list[dict] = []
            for p in (row.get("picks") or []):
                if _pick_allowed(p, allowed_syms) and _schema_ok(p, "pick"):
                    item = _align_pick_name(p, names_by_sym)
                    if item is not None:
                        aligned.append(item)
            row["picks"] = aligned
            aligned_ld: list[dict] = []
            for ld in (row.get("leaders") or []):
                if _pick_allowed(ld, allowed_syms) and _schema_ok(ld, "leader"):
                    item = _align_pick_name(ld, names_by_sym)
                    if item is not None:
                        aligned_ld.append(item)
            row["leaders"] = aligned_ld
        lines.append(row)
    out["main_lines"] = lines
    return out


def mainline_llm(db_path: str, ctx: dict) -> dict:
    """日内主线分析（调用 2）。ctx 含 sector_top/fund_top/ladder/core/etf_sector/candidates。

    无候选股票列表时禁止 picks/leaders；有候选时输出前做 schema/证据校验。
    """
    now = time.time()
    cands = _candidate_entries(ctx)
    cache_key = db_path + "|" + ",".join(sorted(c["symbol"] for c in cands))
    cached = _mainline_cache.get(cache_key)
    if cached and now - cached[0] < _TTL:
        return cached[1]
    try:
        from invest.db import connect

        conn = connect(db_path)
        try:
            cand_text = "、".join(
                f"{c['name']}({c['symbol']})" if c.get("name") else c["symbol"]
                for c in cands
            ) or "（无）"
            pick_schema = (
                '"picks": [], "leaders": []'
                if not cands else
                ('"picks": [{"name": "推荐关注股票名", "symbol": "6位代码", "reason": "推荐理由15字内"}],'
                 '"leaders": [{"role": "连板龙头|趋势龙头|容量龙头|行业龙头",'
                 ' "name": "股票名", "symbol": "6位代码", "analysis": "走势分析（25字内）"}]')
            )
            pick_rule = (
                "禁止输出 picks/leaders（没有候选股票列表）。"
                if not cands else
                f"picks/leaders 只能引用以下规则筛出的标的，禁止编造：{cand_text}。每条必须带 symbol。"
            )
            prompt = (
                "以下是今日盘中板块/资金/连板/ETF/核心池数据：\n"
                f"板块涨幅TOP:\n{ctx.get('sector_top') or '暂无'}\n"
                f"资金净流入TOP:\n{ctx.get('fund_top') or '暂无'}\n"
                f"连板梯队:\n{ctx.get('ladder') or '暂无'}\n"
                f"板块ETF(纯度高于板块指数,体现方向真实强度):\n{ctx.get('etf_sector') or '暂无'}\n"
                f"核心关注实时行情:\n{ctx.get('core') or '暂无'}\n"
                f"规则筛选候选股:\n{cand_text}\n\n"
                "请输出 JSON（只分析 1-3 个最强方向）：\n"
                '{"main_lines": [{"direction": "方向名",'
                '"reason": "上涨原因（结合资金/连板，25字内）",'
                '"internal": "方向内部涨跌分化（大盘vs小盘、细分如医药内创新药vsCXO、机器人内电机vs执行器，25字内）",'
                '"etf": "对应板块ETF强度（涨跌/量能/资金一句话，25字内；无数据写无）",'
                f"{pick_schema},"
                '"outlook": "方向走势推演：一日游/见底反弹/面临分化/缩圈/扩圈量能健康持续/即将分歧（20字内）"}],\n'
                '"core_outlook": "结合指数与板块判断，对核心关注标的今日走势的一句话推演（40字内）"}\n'
                f"{pick_rule}只许引用给定数据，禁止编造个股与数字。"
            )
            out = _llm(conn, _YOUZI_SYSTEM, prompt, max_tokens=2000)
            parsed = _parse_json(out)
            if parsed:
                parsed = _sanitize_mainline(parsed, ctx)
                _mainline_cache[cache_key] = (now, parsed)
                return parsed
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("盘中主线 LLM 失败: %s", exc)
    return {}
