"""Agent 工具注册表：定量层查询 + 观点写入 + 工单发送。"""
from __future__ import annotations

import datetime as dt
import functools
import sqlite3
import threading
import time

from invest.agent import tickets as ticket_mod


def _query(conn: sqlite3.Connection, sql: str, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


# ---------- 个股日线（按需） ----------

_stock_daily_cache: dict[str, tuple[float, dict]] = {}
_STOCK_DAILY_TTL = 1800.0  # 缓存 30 分钟


def _norm_symbol(symbol: str) -> str:
    """归一化为 6 位股票代码（容忍 '600519.SH' / '600519' / 空格）。"""
    s = (symbol or "").strip().upper()
    s = s.split(".")[0] if "." in s else s
    s = "".join(ch for ch in s if ch.isdigit())
    return s if len(s) == 6 else ""


def _daily_stats(symbol: str, source: str, rows: list[tuple]) -> dict:
    """由 [(date, close), ...]（按日期倒序）计算统计。"""
    dates = [str(d) for d, _ in rows]
    closes = [float(c) for _, c in rows if c is not None]
    if not closes or closes[0] <= 0:
        return {"symbol": symbol, "source": source, "error": "无有效收盘数据"}

    def pct_n(n: int):
        return round(closes[0] / closes[n] - 1, 4) if len(closes) > n and closes[n] > 0 else None

    return {
        "symbol": symbol,
        "source": source,  # db=本地库 / akshare=按需联网
        "latest_date": dates[0],
        "latest_close": round(closes[0], 2),
        "pct_1d": pct_n(1),
        "pct_5d": pct_n(5),
        "pct_20d": pct_n(20),
        "high_60d": round(max(closes), 2),
        "low_60d": round(min(closes), 2),
        "last_rows": [{"date": dates[i], "close": round(closes[i], 2)} for i in range(min(5, len(closes)))],
    }


def query_stock_daily(conn: sqlite3.Connection, symbol: str, days: int = 60) -> dict:
    """查询个股日线（最近收盘/涨跌幅/区间高低）。

    优先读本地 daily_bars；本地无该股（非候选池个股）时按需用 akshare 联网拉取
    （东财→新浪双源回退，30 分钟缓存）。
    周期建议（2026-08-18）：短线/游资视角 days=60；中线 days=250；长线 days=500。
    返回 {symbol, latest_close, latest_date, pct_1d/5d/20d, high/low(窗口内), last_rows}。
    """
    sym = _norm_symbol(symbol)
    if not sym:
        return {"error": "请提供 6 位股票代码（如 600519）"}
    days = max(20, min(int(days or 60), 750))

    now = time.time()
    cached = _stock_daily_cache.get(sym)
    if cached and now - cached[0] < _STOCK_DAILY_TTL:
        return cached[1]

    rows = _query(
        conn,
        "SELECT date, close FROM daily_bars WHERE symbol=? ORDER BY date DESC LIMIT ?",
        (sym, days),
    )
    if rows and len(rows) >= 5:
        out = _daily_stats(sym, "db", [(r["date"], r["close"]) for r in rows])
        _stock_daily_cache[sym] = (now, out)
        return out

    # 本地缺失/样本太少 → 按需联网（东财 → 新浪 双源回退，与采集层一致）
    try:
        import akshare as ak

        end = dt.date.today().isoformat().replace("-", "")
        start = (dt.date.today() - dt.timedelta(days=days * 2)).isoformat().replace("-", "")
        df = None
        em_exc = sina_exc = None
        try:
            _df = ak.stock_zh_a_hist(symbol=sym, period="daily",
                                     start_date=start, end_date=end, adjust="qfq")
            if _df is not None and not _df.empty and "收盘" in _df.columns:
                df = _df
        except Exception as exc:
            em_exc = exc
        if df is None:
            try:
                prefix = "sh" if sym.startswith(("6", "9")) else "sz"
                _df = ak.stock_zh_a_daily(symbol=prefix + sym, adjust="qfq")
                if _df is not None and not _df.empty:
                    # 新浪 date 可能是 datetime：统一归一化为 YYYYMMDD 再按起始日过滤
                    _dates = _df["date"].astype(str).str[:10].str.replace("-", "")
                    _df = _df[_dates >= start]
                    df = _df
            except Exception as exc:
                sina_exc = exc
        if df is None or df.empty:
            err = "未查到日线数据（代码可能错误或新股）"
            if em_exc or sina_exc:
                err += f"；东财: {type(em_exc).__name__ if em_exc else '-'}；新浪: {type(sina_exc).__name__ if sina_exc else '-'}"
            out = {"symbol": sym, "source": "akshare", "error": err}
        else:
            # 兼容东财(日期/收盘) 与 新浪(date/close) 两套列名
            date_col = "日期" if "日期" in df.columns else "date"
            close_col = "收盘" if "收盘" in df.columns else "close"
            sub = df.sort_values(date_col, ascending=False).head(days)
            rows2 = [(str(r[date_col])[:10], float(r[close_col])) for _, r in sub.iterrows()]
            out = _daily_stats(sym, "akshare", rows2)
        _stock_daily_cache[sym] = (now, out)
        return out
    except Exception as exc:
        return {"symbol": sym, "source": "akshare", "error": f"日线获取失败: {type(exc).__name__}: {exc}"}


# ---------- 查询工具 ----------
def query_strength(conn, period: str = "short", top: int = 10, obj_type: str = "industry") -> list[dict]:
    return _query(
        conn,
        """SELECT obj, rs, momentum, trend_stage FROM quant_strength
           WHERE period=? AND obj_type=?
             AND run_date = (SELECT MAX(run_date) FROM quant_strength
                             WHERE period=? AND obj_type=?)
           ORDER BY rs DESC LIMIT ?""",
        (period, obj_type, period, obj_type, top),
    )


def query_rotation(conn, top: int = 10) -> list[dict]:
    return _query(conn, "SELECT industry, rank, lead_lag, turnover_share FROM quant_rotation WHERE run_date = (SELECT MAX(run_date) FROM quant_rotation) ORDER BY rank LIMIT ?", (top,))


def query_temperature(conn) -> list[dict]:
    return _query(conn, "SELECT run_date, profit_effect, score FROM quant_temperature ORDER BY run_date DESC LIMIT 1")


def query_capital(conn) -> list[dict]:
    return _query(conn, "SELECT obj, fund_type, style, confidence FROM quant_capital q WHERE run_date = (SELECT MAX(run_date) FROM quant_capital q2 WHERE q2.obj_type = q.obj_type) ORDER BY confidence DESC")


def query_linkage(conn, threshold: float = 0.8, top: int = 10) -> list[dict]:
    return _query(conn, "SELECT a, b, corr, lead FROM quant_linkage WHERE run_date = (SELECT MAX(run_date) FROM quant_linkage) AND corr>=? ORDER BY corr DESC LIMIT ?", (threshold, top))


def query_macro(conn) -> list[dict]:
    return _query(conn, "SELECT date, indicator, value FROM quant_macro ORDER BY date DESC, indicator")


def query_pool(conn) -> list[dict]:
    return _query(conn, "SELECT symbol, level, reason, falsify_condition FROM candidate_pool WHERE out_date IS NULL ORDER BY level, in_date")


# ---------- 写入工具 ----------
def write_viewpoint(
    conn,
    source: str,
    conclusion: str,
    period_tag: str,
    confidence: float,
    evidence: list,
    invalid_condition: str,
    obj_type: str = "",
    obj: str = "",
) -> dict:
    from invest.viewpoints.store import create_viewpoint
    vid = create_viewpoint(
        conn, source=source, conclusion=conclusion, period_tag=period_tag,
        confidence=confidence, evidence=evidence, invalid_condition=invalid_condition,
        obj_type=obj_type, obj=obj,
    )
    return {"viewpoint_id": vid}



def query_realtime_health(conn) -> dict:
    """查询实时行情数据健康状态（数据失效即防守：ok=False 时禁止基于实时价做 P0 决策）。

    2026-08-18 改：**交易时段感知**——非交易时段休市，行情旧属正常，不视为数据失效：
    返回 ok=True 并提示改用日线/收盘数据（避免 Agent 在盘后/盘前误报"数据失效"）。
    """
    from invest.config import get_settings
    from invest.intraday import _in_trading_window

    if not _in_trading_window():
        return {
            "ok": True,
            "stale": 0,
            "note": "非交易时段（休市），不检查实时行情；请使用日线/收盘数据（daily_bars）分析，"
                    "不要以实时价做盘中结论。",
        }
    from invest.data.realtime import realtime_health

    return realtime_health(get_settings().db_path)


def query_data_freshness(conn) -> dict:
    """数据新鲜度总览（2026-08-20）：daily_bars/index_bars/quant 最新时点 vs 最近交易日。

    回复涉及具体行情/板块/个股数据前先调用：fresh=False 时数据滞后，
    应说明"数据截至 XX"再回答，不要假装是当天最新。
    """
    import datetime as dt

    from invest.data.calendar import latest_trading_day

    exp = latest_trading_day(dt.date.today()).isoformat()
    latest_bars = conn.execute("SELECT MAX(date) FROM daily_bars").fetchone()[0] or ""
    latest_idx = conn.execute("SELECT MAX(date) FROM index_bars").fetchone()[0] or ""
    latest_q = conn.execute("SELECT MAX(run_date) FROM quant_strength").fetchone()[0] or ""
    stale = [k for k, v in (("daily_bars", latest_bars), ("index_bars", latest_idx), ("quant", latest_q)) if v < exp]
    latest_all = max(latest_bars, latest_idx, latest_q) or "无"
    return {
        "expected_trading_day": exp,
        "daily_bars_latest": latest_bars or "无",
        "index_bars_latest": latest_idx or "无",
        "quant_latest": latest_q or "无",
        "fresh": not stale,
        "stale_parts": stale,
        "note": "数据已是最新" if not stale
                else f"数据滞后：{','.join(stale)}（最新到 {latest_all}）；当日日线/板块数据源通常晚间才发布",
    }


def web_search(query: str, n: int = 5) -> list[dict] | dict:
    """联网搜索（2026-08-21）：必应检索最新资讯/财报/新闻。返回 [{title,url,snippet}]。"""
    from invest.agent.web_tools import web_search as _ws

    return _ws(query, n=n)


def web_fetch(url: str) -> dict:
    """抓取网页正文（2026-08-21）。返回 {url, text}。"""
    from invest.agent.web_tools import web_fetch as _wf

    return _wf(url)


def run_skill(symbol: str, depth: str = "lite") -> dict:
    """跑 UZI 深度分析流水线（2026-08-21）：多维数据→LLM 多轮→HTML 报告。
    返回 {ok, report_path, summary}；depth: lite(快)/medium/deep。

    2026-08-21 异步化：注册了 run_skill sink（飞书场景）时，改为后台线程执行，
    立即返回"已启动"提示（不阻塞对话线程），完成后由 sink 把结果发回原会话；
    未注册 sink（脚本/测试）时保持同步原逻辑。
    """
    if _run_skill_sink is None:
        from invest.agent.skill_runner import run_skill as _rs

        return _rs(symbol, depth=depth)
    # 异步：后台线程执行，完成后回调 sink(结果, chat_id)
    import threading

    chat_id = getattr(_thread_chat, "chat_id", "")
    depth = depth if depth in ("lite", "medium", "deep") else "lite"
    est = {"lite": "约5-10分钟", "medium": "约10-15分钟", "deep": "约15-20分钟"}[depth]

    def _worker() -> None:
        from invest.agent.skill_runner import run_skill as _rs

        try:
            result = _rs(symbol, depth=depth)
        except Exception as exc:
            result = {"ok": False, "error": f"深度分析异常: {type(exc).__name__}: {exc}"}
        try:
            _run_skill_sink(result, chat_id)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("run_skill 结果回调失败: %s", exc)

    threading.Thread(target=_worker, daemon=True, name="run-skill").start()
    return {
        "ok": True,
        "async": True,
        "note": f"深度分析已启动（{depth} 档，{est}），完成后会自动把报告摘要与路径发给你。",
    }


# ---- run_skill 异步 sink（飞书场景注册，见 invest/push/feishu_ws.py） ----

_run_skill_sink = None  # callable(result: dict, chat_id: str) -> None
_thread_chat = threading.local()


def set_run_skill_sink(fn) -> None:
    """注册 run_skill 异步完成回调（feishu_ws 启动时调用）。fn(result, chat_id)。"""
    global _run_skill_sink
    _run_skill_sink = fn


def set_current_chat(chat_id: str) -> None:
    """记录当前处理消息的会话 id（feishu_ws 事件线程内调用，thread-local）。"""
    _thread_chat.chat_id = chat_id


def cross_validate(conn, obj: str, obj_type: str = "industry") -> dict:
    """多源交叉验证（A-Stock-Skills 多源校验思想，2026-08-16）。

    对 obj（行业名或个股代码）一次性汇总四个独立维度的最新信号：
    - strength: 相对强度 RS / 趋势阶段 / 动量（短线轨）；
    - capital:   资金属性 / 风格标签；
    - linkage:   高相关联动板块（相关度≥0.7）；
    - valuation: 估值分位（PE/PB）与拥挤度（行业）；
    返回 {obj, dimensions: {...}, n_dimensions, summary}。
    """
    out: dict = {}
    if obj_type == "stock":
        # 个股：强度 + 资金 + 行业维度（不覆盖个股自身维度）
        try:
            rows = _query(
                conn,
                """SELECT obj, rs, trend_stage FROM quant_strength
                   WHERE obj_type='stock' AND period='short' AND obj=?
                     AND run_date = (SELECT MAX(run_date) FROM quant_strength
                                     WHERE obj_type='stock' AND period='short' AND obj=?)""",
                (obj, obj),
            )
            out["strength"] = rows[0] if rows else None
        except Exception:
            out["strength"] = None
        try:
            rows = _query(
                conn,
                """SELECT fund_type, style, confidence FROM quant_capital
                   WHERE obj_type='stock' AND obj=?
                     AND run_date = (SELECT MAX(run_date) FROM quant_capital
                                     WHERE obj_type='stock' AND obj=?)""",
                (obj, obj),
            )
            out["capital"] = rows[0] if rows else None
        except Exception:
            out["capital"] = None
        # 个股→行业（手工映射）→ 行业维度放进 industry 子键，不覆盖个股维度
        try:
            from invest.data.industry_map import industry_of
            ind = industry_of(conn, obj)
            if ind:
                out["industry"] = {"name": ind, **_industry_dimensions(conn, ind)}
        except Exception:
            pass
    else:
        out.update(_industry_dimensions(conn, obj))
    n = sum(1 for v in out.values() if v is not None and v != {})
    return {"obj": obj, "obj_type": obj_type, "dimensions": out, "n_dimensions": n}


def _industry_dimensions(conn, industry: str) -> dict:
    """行业四维度：强度/资金/联动/估值。"""
    res: dict = {}
    try:
        rows = _query(
            conn,
            """SELECT obj, rs, rs5, rs10, rs20, momentum, trend_stage FROM quant_strength
               WHERE obj_type='industry' AND period='short' AND obj=?
                 AND run_date = (SELECT MAX(run_date) FROM quant_strength
                                 WHERE obj_type='industry' AND period='short')""",
            (industry,),
        )
        res["strength"] = rows[0] if rows else None
    except Exception:
        res["strength"] = None
    try:
        rows = _query(
            conn,
            """SELECT fund_type, style, confidence FROM quant_capital
               WHERE obj_type='industry' AND obj=?
                 AND run_date = (SELECT MAX(run_date) FROM quant_capital
                                 WHERE obj_type='industry' AND obj=?)""",
            (industry, industry),
        )
        res["capital"] = rows[0] if rows else None
    except Exception:
        res["capital"] = None
    try:
        rows = _query(
            conn,
            """SELECT a, b, corr, lead FROM quant_linkage
               WHERE run_date = (SELECT MAX(run_date) FROM quant_linkage)
                 AND (a=? OR b=?) AND corr>=0.7 ORDER BY corr DESC LIMIT 5""",
            (industry, industry),
        )
        res["linkage"] = rows
    except Exception:
        res["linkage"] = []
    try:
        rows = _query(
            conn,
            """SELECT pe_pct, pb_pct, crowding, crowding_state FROM quant_valuation
               WHERE obj=?
                 AND run_date = (SELECT MAX(run_date) FROM quant_valuation WHERE obj=?)""",
            (industry, industry),
        )
        res["valuation"] = rows[0] if rows else None
    except Exception:
        res["valuation"] = None
    return res


def send_direction_hint(conn, direction: str, obj: str, reason: str) -> dict:
    tid = ticket_mod.create_ticket(
        conn, "direction_hint", from_agent="research", to_agent="trade",
        direction=direction, payload={"obj": obj, "reason": reason},
    )
    return {"ticket_id": tid}


def request_attribution(conn, obj: str, reason: str) -> dict:
    tid = ticket_mod.create_ticket(
        conn, "attribution_request", from_agent="trade", to_agent="research",
        payload={"obj": obj, "reason": reason},
    )
    return {"ticket_id": tid}


TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "query_strength", "description": "查询相对强度榜（短线/中线轨；默认行业）", "parameters": {"type": "object", "properties": {"period": {"type": "string", "enum": ["short", "mid"]}, "top": {"type": "integer"}, "obj_type": {"type": "string", "enum": ["industry", "stock"]}}, "required": []}}},
    {"type": "function", "function": {"name": "query_rotation", "description": "查询板块轮动排名与领涨滞后", "parameters": {"type": "object", "properties": {"top": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "query_temperature", "description": "查询市场温度", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "query_capital", "description": "查询行业资金属性与风格", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "query_linkage", "description": "查询行业高相关联动对", "parameters": {"type": "object", "properties": {"threshold": {"type": "number"}, "top": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "query_macro", "description": "查询宏观流动性加工指标", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "query_pool", "description": "查询候选池与关注度", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "write_viewpoint", "description": "写入结构化观点（必须含五要素）", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "conclusion": {"type": "string"}, "period_tag": {"type": "string", "enum": ["micro", "short", "mid", "long"]}, "confidence": {"type": "number"}, "evidence": {"type": "array", "items": {"type": "object"}}, "invalid_condition": {"type": "string"}, "obj_type": {"type": "string"}, "obj": {"type": "string"}}, "required": ["source", "conclusion", "period_tag", "confidence", "evidence", "invalid_condition"]}}},
    {"type": "function", "function": {"name": "send_direction_hint", "description": "投研→交易：方向提示单", "parameters": {"type": "object", "properties": {"direction": {"type": "string"}, "obj": {"type": "string"}, "reason": {"type": "string"}}, "required": ["direction", "obj", "reason"]}}},
    {"type": "function", "function": {"name": "query_realtime_health", "description": "查询实时行情数据健康状态；ok=false 表示行情失效/过期，此时禁止基于实时价格给出开仓或止损决策", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "request_attribution", "description": "交易→投研：归因请求单", "parameters": {"type": "object", "properties": {"obj": {"type": "string"}, "reason": {"type": "string"}}, "required": ["obj", "reason"]}}},
    {"type": "function", "function": {"name": "cross_validate", "description": "多源交叉验证：对某行业或个股汇总四维度最新信号（强度RS/趋势 / 资金风格 / 高相关联动 / 估值PE/PB分位与拥挤度），用于确认方向是否多维度共振", "parameters": {"type": "object", "properties": {"obj": {"type": "string"}, "obj_type": {"type": "string", "enum": ["industry", "stock"], "description": "默认 industry"}}, "required": ["obj"]}}},
    {"type": "function", "function": {"name": "query_stock_daily", "description": "查询任意个股日线数据（最近收盘价/1/5/20日涨跌幅/窗口内高低/最近5条K线）。本地无该股数据时会按需联网拉取（akshare 东财→新浪）。周期决定 days：短线/游资视角=60；中线=250；长线=500。分析个股时优先用这个工具拿收盘数据，再配 cross_validate 看多维度", "parameters": {"type": "object", "properties": {"symbol": {"type": "string", "description": "6位股票代码，如 600519"}, "days": {"type": "integer", "description": "交易日数：短线60/中线250/长线500，默认60"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "query_data_freshness", "description": "数据新鲜度总览：daily_bars/index_bars/quant 最新时点 vs 最近交易日。回答涉及具体行情/板块/个股数据的问题前必须先调用；fresh=false 时说明数据截至时间再回答", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "web_search", "description": "联网搜索（必应）：查最新资讯/财报/公告/新闻/政策。系统本地数据查不到的最新信息用这个", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}, "n": {"type": "integer", "description": "结果条数，默认5"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "web_fetch", "description": "抓取指定网页正文（用于看搜索到的链接详情）", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "run_skill", "description": "跑 UZI 深度分析流水线（多维数据→LLM 多轮→HTML 报告）。用户要求'深度分析/完整报告/UZI'时用；depth=lite 快（约1分钟）", "parameters": {"type": "object", "properties": {"symbol": {"type": "string", "description": "6位股票代码或名称"}, "depth": {"type": "string", "enum": ["lite", "medium", "deep"]}}, "required": ["symbol"]}}},
]

_IMPLEMENTATIONS = {
    "query_strength": query_strength,
    "query_rotation": query_rotation,
    "query_temperature": query_temperature,
    "query_capital": query_capital,
    "query_linkage": query_linkage,
    "query_macro": query_macro,
    "query_pool": query_pool,
    "write_viewpoint": write_viewpoint,
    "send_direction_hint": send_direction_hint,
    "request_attribution": request_attribution,
    "query_realtime_health": query_realtime_health,
    "cross_validate": cross_validate,
    "query_stock_daily": query_stock_daily,
    "query_data_freshness": query_data_freshness,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "run_skill": run_skill,
}


def build_dispatch(conn: sqlite3.Connection, source: str = "research") -> dict:
    """把工具绑定到指定数据库连接；写观点工具来源由服务端固定注入。

    LLM 即使传 source 参数也会被覆盖，避免观点来源绕过仲裁与准确率统计。
    """
    # 非 DB 工具（联网/技能）第一个参数不是 conn，不能 partial(conn)
    no_conn = {"web_search", "web_fetch", "run_skill"}
    out = {
        name: (functools.partial(fn, conn) if name not in no_conn else fn)
        for name, fn in _IMPLEMENTATIONS.items()
    }

    def _write(**kwargs):
        kwargs.pop("source", None)
        return write_viewpoint(conn, source=source, **kwargs)

    out["write_viewpoint"] = _write
    return out