"""全链路流水线：采集 → 定量 → Agent → 推送（供调度器与手动脚本复用）。"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from invest.db import connect, init_db

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def build_collect_tasks(db_path: str) -> list[dict]:
    """基础任务 + 候选池个股批量日线任务。"""
    from invest.data.collector import TASKS
    tasks = list(TASKS)
    conn = connect(db_path)
    try:
        symbols = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE out_date IS NULL ORDER BY level, in_date"
        )]
    finally:
        conn.close()
    if symbols:
        tasks.append({
            "name": "stock_daily_all",
            "kind": "stock_daily_all",
            "table": "daily_bars",
            "sources": ["akshare", "tushare"],
            "cross_check": False,
            "params": {"symbols": symbols, "start_date": "20240101", "end_date": "20991231"},
        })
        tasks.append({
            "name": "seat_detail",
            "kind": "seat_detail",
            "table": "dragon_tiger",
            "sources": ["akshare"],
            "cross_check": False,
            "params": {"symbols": symbols, "days": 5},
        })
    return tasks


def collect(db_path: str, tasks=None) -> list[dict]:
    """采集数据（默认含候选池个股任务），返回任务摘要。"""
    from invest.data.collector import run_collection
    if tasks is None:
        tasks = build_collect_tasks(db_path)
    return run_collection(db_path, tasks=tasks)


def quant(db_path: str) -> dict:
    """计算并写入全部定量表，返回行数统计。"""
    from invest.quant.capital import compute_capital
    from invest.quant.indicators import get_params
    from invest.quant.crowding import compute_crowding
    from invest.quant.linkage import compute_linkage
    from invest.quant.macro_liquidity import compute_macro_liquidity
    from invest.quant.rotation import compute_rotation
    from invest.quant.strength import compute_strength
    from invest.quant.temperature import compute_temperature
    from invest.quant.weekly import compute_weekly

    init_db(db_path)
    conn = connect(db_path)
    try:
        ind = pd.read_sql_query(
            "SELECT date, industry, close, amount FROM industry_bars ORDER BY date", conn,
        )
        idx = pd.read_sql_query(
            "SELECT date, close FROM index_bars WHERE index_code='000300' ORDER BY date", conn,
        )
        macro = pd.read_sql_query(
            "SELECT date, indicator, value FROM macro_series ORDER BY date", conn,
        )
        emotion = pd.read_sql_query(
            "SELECT date, limit_up_count, max_lianban, zhaban_rate FROM market_emotion ORDER BY date", conn,
        )
        stock = pd.read_sql_query(
            "SELECT date, symbol, close FROM daily_bars ORDER BY date", conn,
        )
        valuation_hist = pd.read_sql_query(
            "SELECT date, industry, pe FROM industry_valuation ORDER BY date", conn,
        )
    finally:
        conn.close()
    ind["date"] = pd.to_datetime(ind["date"], format="mixed", errors="coerce")
    idx["date"] = pd.to_datetime(idx["date"], format="mixed", errors="coerce")
    closes = ind.pivot_table(index="date", columns="industry", values="close")
    amounts = ind.pivot_table(index="date", columns="industry", values="amount")
    benchmark = idx.set_index("date")["close"]
    returns = closes.pct_change().replace([float("inf"), float("-inf")], float("nan"))
    returns = returns.mask(returns.abs() > 0.15)  # 单日>15% 视为数据异常（板块指数不可能），排除防污染

    stock["date"] = pd.to_datetime(stock["date"], format="mixed", errors="coerce")
    stock_closes = stock.pivot_table(index="date", columns="symbol", values="close")
    stock_results = {}
    if len(stock_closes.columns) >= 1:
        stock_returns = stock_closes.pct_change().replace([float("inf"), float("-inf")], float("nan"))
        stock_returns = stock_returns.mask(stock_returns.abs() > 0.15)
        conn2 = connect(db_path)
        try:
            seat_rows = pd.read_sql_query(
                "SELECT symbol, seat_type, buy, sell, net FROM dragon_tiger WHERE seat_type IS NOT NULL", conn2,
            )
        finally:
            conn2.close()
        from invest.quant.capital import aggregate_fund_types, compute_stock_capital
        fund_types = aggregate_fund_types(seat_rows)
        stock_results["stock_strength"] = compute_strength(stock_closes, benchmark, obj_type="stock")
        stock_results["stock_weekly"] = compute_weekly(stock_closes, benchmark, obj_type="stock")
        stock_results["stock_capital"] = compute_stock_capital(stock_closes, stock_returns, fund_types)
    if len(stock_closes.columns) >= 3:
        stock_results["stock_linkage"] = compute_linkage(stock_returns, **get_params("linkage"))

    results = {
        "strength": compute_strength(closes, benchmark),
        "weekly": compute_weekly(closes, benchmark),
        "rotation": compute_rotation(returns, amounts),
        "temperature": compute_temperature(returns, amounts, emotion=emotion),
        "capital": compute_capital(closes, returns),
        "linkage": compute_linkage(returns, **get_params("linkage")),
        "crowding": compute_crowding(amounts),
        "macro": compute_macro_liquidity(macro),
    }
    from invest.quant.valuation import compute_pe_percentile, merge_valuation
    if not results["crowding"].empty and not valuation_hist.empty:
        pct = compute_pe_percentile(valuation_hist)
        results["crowding"] = merge_valuation(results["crowding"], pct)

    conn = connect(db_path)
    try:
        from invest.data.storage import upsert_df
        upsert_df(conn, "quant_strength", results["strength"])
        upsert_df(conn, "quant_strength", results["weekly"])
        if "stock_strength" in stock_results:
            upsert_df(conn, "quant_strength", stock_results["stock_strength"])
        if "stock_weekly" in stock_results:
            upsert_df(conn, "quant_strength", stock_results["stock_weekly"])
        if "stock_linkage" in stock_results:
            upsert_df(conn, "quant_linkage", stock_results["stock_linkage"])
        if "stock_capital" in stock_results:
            upsert_df(conn, "quant_capital", stock_results["stock_capital"])
        upsert_df(conn, "quant_rotation", results["rotation"])
        upsert_df(conn, "quant_temperature", results["temperature"])
        upsert_df(conn, "quant_capital", results["capital"])
        upsert_df(conn, "quant_linkage", results["linkage"])
        upsert_df(conn, "quant_valuation", results["crowding"])
        upsert_df(conn, "quant_macro", results["macro"])
    finally:
        conn.close()
    results.update(stock_results)
    return {k: len(v) for k, v in results.items()}


def agent_premarket(db_path: str) -> str:
    from invest.agent.agents import run_research
    conn = connect(db_path)
    try:
        return run_research(conn, "基于当前宏观流动性、市场温度与候选池，生成今日关注清单（最多5条方向，含周期与失效条件）")
    finally:
        conn.close()


def agent_after_close(db_path: str) -> str:
    from invest.agent.agents import run_trade
    conn = connect(db_path)
    try:
        return run_trade(conn, "复盘行业强度榜：识别强度异动行业，必要时发起归因请求，输出1-3条短线轨观点")
    finally:
        conn.close()


def arbitrate_all(db_path: str) -> int:
    from invest.agent.arbiter import arbitrate, find_conflicts
    conn = connect(db_path)
    try:
        pairs = find_conflicts(conn)
        for a, b in pairs:
            try:
                arbitrate(conn, a, b)
            except Exception as exc:  # noqa: BLE001
                logger.warning("仲裁 %s/%s 失败: %s", a, b, exc)
        return len(pairs)
    finally:
        conn.close()


# ---------- 消息模板 ----------
def _pct(v) -> str:
    return f"{v:+.1%}" if v is not None and pd.notna(v) else "-"


def _top_strength(conn, period: str = "short", n: int = 5) -> str:
    """行业强度榜文本；短线轨附 5/10/20 日相对强度（RS）。"""
    if period == "short":
        rows = conn.execute(
            """SELECT obj, rs, rs5, rs10, rs20, trend_stage FROM quant_strength
               WHERE period=? AND obj_type='industry'
                 AND run_date = (SELECT MAX(run_date) FROM quant_strength
                                 WHERE period=? AND obj_type='industry')
               ORDER BY rs DESC LIMIT ?""",
            (period, period, n),
        ).fetchall()
        parts = []
        for r in rows:
            windows = " ".join(
                f"{h}{_pct(r[k])}" for h, k in (("5日", "rs5"), ("10日", "rs10"), ("20日", "rs20"))
            )
            parts.append(f"{r['obj']} rs{_pct(r['rs'])} [{windows}] {r['trend_stage']}")
        return "\n".join(parts) or "-"
    rows = conn.execute(
        """SELECT obj, rs, trend_stage FROM quant_strength
           WHERE period=? AND obj_type='industry'
             AND run_date = (SELECT MAX(run_date) FROM quant_strength
                             WHERE period=? AND obj_type='industry')
           ORDER BY rs DESC LIMIT ?""",
        (period, period, n),
    ).fetchall()
    return "；".join(f"{r['obj']}({r['rs']:+.1%},{r['trend_stage']})" for r in rows) or "-"


def _movers(conn) -> list[dict]:
    """最新两个行业交易日各板块涨跌幅与前一日涨跌幅（供涨幅/跌幅榜与新晋标记）。"""
    rows = conn.execute(
        """WITH ranked AS (
             SELECT industry, close,
                    ROW_NUMBER() OVER (PARTITION BY industry ORDER BY REPLACE(date,'-','') DESC) rn
             FROM industry_bars
           )
           SELECT a.industry,
                  (a.close/b.close - 1) AS pct,
                  (b.close/c.close - 1) AS prev_pct
           FROM ranked a
           JOIN ranked b ON a.industry=b.industry AND b.rn=2
           JOIN ranked c ON a.industry=c.industry AND c.rn=3
           WHERE a.rn=1"""
    ).fetchall()
    return [dict(r) for r in rows]


def _mover_tag(industry: str, rs_map: dict, trend_map: dict, prev_top: set) -> str:
    """涨幅榜性质标签：趋势强 / 超跌反弹 / 新启动 / 新晋。"""
    tags = []
    rs5, rs10, rs20 = rs_map.get(industry, (None, None, None))
    stage = trend_map.get(industry)
    if rs20 is not None and pd.notna(rs20):
        if rs20 > 0:
            tags.append("趋势强")
        elif rs5 is not None and pd.notna(rs5) and rs5 > 0:
            tags.append("超跌反弹")
    if stage == "启动":
        tags.append("新启动")
    if industry not in prev_top:
        tags.append("新晋")
    return "/".join(tags)


def _daily_movers_block(conn, n: int = 5) -> str:
    """当日板块涨幅/跌幅榜（各前 n）；涨幅榜附趋势性质与新晋标记。"""
    rows = _movers(conn)
    if not rows:
        return "当日涨幅前5:\n-\n当日跌幅前5:\n-"
    rs_rows = conn.execute(
        """SELECT obj, rs5, rs10, rs20, trend_stage FROM quant_strength
           WHERE period='short' AND obj_type='industry'
             AND run_date = (SELECT MAX(run_date) FROM quant_strength
                             WHERE period='short' AND obj_type='industry')"""
    ).fetchall()
    rs_map = {r["obj"]: (r["rs5"], r["rs10"], r["rs20"]) for r in rs_rows}
    trend_map = {r["obj"]: r["trend_stage"] for r in rs_rows}
    by_pct = sorted(rows, key=lambda r: r["pct"] if r["pct"] is not None else -1e9, reverse=True)
    prev_top = {
        r["industry"]
        for r in sorted(rows, key=lambda r: r["prev_pct"] if r["prev_pct"] is not None else -1e9, reverse=True)[:n]
    }
    up = []
    for r in by_pct[:n]:
        if r["pct"] is None:
            continue
        tag = _mover_tag(r["industry"], rs_map, trend_map, prev_top)
        up.append(f"{r['industry']} {r['pct']:+.2%}" + (f" [{tag}]" if tag else ""))
    down = [f"{r['industry']} {r['pct']:+.2%}" for r in by_pct[-n:][::-1] if r["pct"] is not None]
    return "当日涨幅前5:\n" + "\n".join(up or ["-"]) + "\n当日跌幅前5:\n" + "\n".join(down or ["-"])


def _breadth(conn) -> str:
    """最新行业交易日板块上涨/下跌家数。"""
    rows = _movers(conn)
    if not rows:
        return "-"
    up = sum(1 for r in rows if (r["pct"] or 0) > 0)
    down = sum(1 for r in rows if (r["pct"] or 0) < 0)
    return f"上涨{up}/下跌{down}"


def _temperature(conn) -> str:
    """市场温度：现值 + 较上日趋势 + 5/20日前 + 宽度。"""
    rows = conn.execute(
        "SELECT run_date, score, profit_effect FROM quant_temperature ORDER BY run_date DESC LIMIT 21"
    ).fetchall()
    if not rows:
        return "-"
    latest = rows[0]
    parts = [f"{latest['score']:.0f}/100"]
    if len(rows) >= 2:
        d = float(latest["score"]) - float(rows[1]["score"])
        trend = "升温" if d > 1 else ("降温" if d < -1 else "持平")
        parts.append(f"{trend}(较上日{d:+.0f})")
    if len(rows) >= 6:
        parts.append(f"5日前{rows[5]['score']:.0f}")
    if len(rows) >= 21:
        parts.append(f"20日前{rows[20]['score']:.0f}")
    if latest["profit_effect"] is not None:
        parts.append(f"宽度{latest['profit_effect']:.0%}")
    return " | ".join(parts)


def _freshness(conn) -> str:
    """数据新鲜度：板块/指数最新日期与滞后天数。"""
    import datetime as dt

    def _fmt(d) -> str:
        if not d:
            return "-"
        try:
            dd = dt.date.fromisoformat(d)
        except ValueError:
            try:
                dd = dt.date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:]}")
            except Exception:  # noqa: BLE001
                return str(d)
        days = (dt.date.today() - dd).days
        return f"{d}({'今日' if days <= 0 else f'滞后{days}天'})"

    ind = conn.execute("SELECT MAX(date) FROM industry_bars").fetchone()[0]
    idx = conn.execute("SELECT MAX(date) FROM index_bars").fetchone()[0]
    return f"板块{_fmt(ind)} | 指数{_fmt(idx)}"


def _ratings_block(conn) -> str:
    """评级 + 与上一评级日对比的变化。"""
    macro = conn.execute(
        "SELECT date, value FROM ratings WHERE kind='macro' ORDER BY date DESC LIMIT 2"
    ).fetchall()
    market = conn.execute(
        "SELECT date, value FROM ratings WHERE kind='market' ORDER BY date DESC LIMIT 2"
    ).fetchall()
    cur_macro = macro[0]["value"] if macro else None
    cur_market = market[0]["value"] if market else None
    base = f"宏观={cur_macro or '未评'} 市场={cur_market or '未评'}"
    changes = []
    if len(macro) == 2 and macro[0]["value"] != macro[1]["value"]:
        changes.append(f"宏观 {macro[1]['value']}→{macro[0]['value']}")
    if len(market) == 2 and market[0]["value"] != market[1]["value"]:
        changes.append(f"市场 {market[1]['value']}→{market[0]['value']}")
    if changes:
        base += f"（{'；'.join(changes)}）"
    return base


def _agent_viewpoints(conn, n: int = 5) -> str:
    """最新 active 的投研/交易观点（结论/周期/失效条件），替代原始大段文本。"""
    _period = {"micro": "超短", "short": "短线", "mid": "中线", "long": "长线"}
    rows = conn.execute(
        """SELECT obj, conclusion, period_tag, invalid_condition FROM viewpoints
           WHERE status='active' AND source IN ('research','trade')
           ORDER BY created_at DESC LIMIT ?""",
        (n,),
    ).fetchall()
    lines = []
    for r in rows:
        obj = f"[{r['obj']}] " if r["obj"] else ""
        period = _period.get(r["period_tag"], r["period_tag"] or "")
        fail = f"（失效:{r['invalid_condition']}）" if r["invalid_condition"] else ""
        lines.append(f"- {obj}{r['conclusion']} [{period}]{fail}")
    return "\n".join(lines)


def notify_premarket(db_path: str, agent_text: str = "") -> bool:
    conn = connect(db_path)
    try:
        msg = (
            f"【A股投资系统 · 盘前】\n"
            f"数据截至: {_freshness(conn)}\n"
            f"评级: {_ratings_block(conn)}\n"
            f"市场温度: {_temperature(conn)}\n"
            f"板块宽度: {_breadth(conn)}\n"
            f"关注方向(Agent):\n{agent_text or '[Agent 未运行]'}"
        )
    finally:
        conn.close()
    from invest.notifier import Notifier
    return Notifier().send_text(msg, key="premarket", min_interval=600)


def notify_after_close(db_path: str, agent_text: str = "") -> bool:
    conn = connect(db_path)
    try:
        new_vp = conn.execute(
            "SELECT COUNT(*) AS n FROM viewpoints WHERE date(created_at)=date('now','localtime')"
        ).fetchone()["n"]
        stop_hits = conn.execute(
            """SELECT COUNT(*) AS n FROM trade_records
               WHERE date(created_at)=date('now','localtime') AND deviation_note LIKE '%止损%'"""
        ).fetchone()["n"]
        msg = (
            f"【A股投资系统 · 盘后日报】\n"
            f"数据截至: {_freshness(conn)}\n"
            f"市场温度: {_temperature(conn)}\n"
            f"板块宽度: {_breadth(conn)}\n"
            f"评级: {_ratings_block(conn)}\n"
            f"{_daily_movers_block(conn)}\n"
            f"短线强度前5（RS 5/10/20日超额）:\n{_top_strength(conn, 'short')}\n"
            f"今日新增观点: {new_vp} 条 | 触发止损: {stop_hits} 笔\n"
            f"Agent 复盘:\n{_agent_viewpoints(conn) or agent_text or '[Agent 未运行]'}"
        )
    finally:
        conn.close()
    from invest.notifier import Notifier
    return Notifier().send_text(msg, key="after_close", min_interval=600)


def notify_weekend(db_path: str, agent_text: str = "") -> bool:
    conn = connect(db_path)
    try:
        msg = (
            f"【A股投资系统 · 周报】\n"
            f"数据截至: {_freshness(conn)}\n"
            f"评级: {_ratings_block(conn)}\n"
            f"市场温度: {_temperature(conn)}\n"
            f"中线轨强度前5: {_top_strength(conn, 'mid')}\n"
            f"宏观流动性:\n{_macro_text(conn)}\n"
            f"周度观点:\n{_agent_viewpoints(conn) or agent_text or '[Agent 未运行]'}"
        )
    finally:
        conn.close()
    from invest.notifier import Notifier
    return Notifier().send_text(msg, key="weekend", min_interval=600)


def _macro_text(conn) -> str:
    rows = conn.execute(
        "SELECT indicator, value FROM quant_macro ORDER BY date DESC, indicator"
    ).fetchall()
    return "；".join(f"{r['indicator']}={r['value']}" for r in rows) or "-"