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


def _top_daily_gainers(conn, n: int = 5) -> str:
    """最新一个行业数据日，板块指数真实涨幅前 n。"""
    rows = conn.execute(
        """WITH ranked AS (
             SELECT industry, close,
                    ROW_NUMBER() OVER (PARTITION BY industry ORDER BY REPLACE(date,'-','') DESC) rn
             FROM industry_bars
           )
           SELECT a.industry, (a.close/b.close - 1) AS pct
           FROM ranked a JOIN ranked b
             ON a.industry=b.industry AND a.rn=1 AND b.rn=2
           ORDER BY pct DESC LIMIT ?""",
        (n,),
    ).fetchall()
    return "\n".join(f"{r['industry']} {r['pct']:+.2%}" for r in rows) or "-"


def _temperature(conn) -> str:
    row = conn.execute(
        "SELECT score, profit_effect FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
    ).fetchone()
    return f"{row['score']:.0f}/100（宽度 {row['profit_effect']:.0%}）" if row else "-"


def _ratings(conn) -> str:
    macro = conn.execute("SELECT value FROM ratings WHERE kind='macro' ORDER BY date DESC LIMIT 1").fetchone()
    market = conn.execute("SELECT value FROM ratings WHERE kind='market' ORDER BY date DESC LIMIT 1").fetchone()
    return f"宏观={macro['value'] if macro else '未评'} 市场={market['value'] if market else '未评'}"


def _latest_data_date(conn) -> str:
    row = conn.execute("SELECT MAX(date) AS d FROM industry_bars").fetchone()
    return row["d"] or "-"


def notify_premarket(db_path: str, agent_text: str = "") -> bool:
    conn = connect(db_path)
    try:
        msg = (
            f"【A股投资系统 · 盘前】\n"
            f"数据截至: {_latest_data_date(conn)}\n"
            f"评级: {_ratings(conn)}\n"
            f"市场温度: {_temperature(conn)}\n"
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
            f"数据截至: {_latest_data_date(conn)}\n"
            f"温度: {_temperature(conn)}\n"
            f"当日涨幅前5:\n{_top_daily_gainers(conn)}\n"
            f"短线强度前5（RS 5/10/20日超额）:\n{_top_strength(conn, 'short')}\n"
            f"今日新增观点: {new_vp} 条 | 触发止损: {stop_hits} 笔\n"
            f"Agent 复盘:\n{agent_text or '[Agent 未运行]'}"
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
            f"数据截至: {_latest_data_date(conn)}\n"
            f"评级: {_ratings(conn)}\n"
            f"中线轨强度前5: {_top_strength(conn, 'mid')}\n"
            f"宏观流动性:\n{_macro_text(conn)}\n"
            f"周度观点:\n{agent_text or '[Agent 未运行]'}"
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