"""历史行业归属/成分/ST 状态按历史时点保存（TODO [A]10，2026-08-15）。

数据源成本评估后确定回填范围（成分股全量需东财成分接口，被拦时用手工映射）；
v1 实现：每日收盘把「候选池标的 + 手工映射标的」的 行业/ST 状态快照落库
（stock_universe_history），供任意历史时点回溯（快照重建的组成部分）。
"""
from __future__ import annotations

import datetime as dt
import sqlite3

from invest.data.industry_map import load_industry_stocks


def _is_st(symbol: str) -> bool:
    s = str(symbol).upper()
    return s.startswith(("ST", "*ST")) or s.endswith("退")


def record_universe_snapshot(
    conn: sqlite3.Connection,
    date: str | None = None,
    mapping: dict | None = None,
) -> int:
    """记录历史时点快照：候选池标的 + 手工映射标的 → 行业/ST 状态。

    返回写入行数。同日重复调用先删后插（幂等）。
    """
    date = date or dt.date.today().isoformat()
    mapping = mapping if mapping is not None else load_industry_stocks()

    rows: dict[str, dict] = {}
    for sym, ind in mapping.items():
        rows[str(sym)] = {"industry": str(ind), "is_st": int(_is_st(sym))}
    pool = conn.execute(
        "SELECT symbol, industry FROM candidate_pool WHERE out_date IS NULL"
    ).fetchall()
    for r in pool:
        sym = str(r["symbol"])
        ind = r["industry"] or rows.get(sym, {}).get("industry", "")
        rows[sym] = {"industry": ind, "is_st": int(_is_st(sym))}

    with conn:
        conn.execute("DELETE FROM stock_universe_history WHERE date=?", (date,))
        for sym, info in rows.items():
            conn.execute(
                """INSERT INTO stock_universe_history(date, symbol, industry, is_st, src)
                   VALUES(?,?,?,?,'snapshot')""",
                (date, sym, info["industry"], info["is_st"]),
            )
    return len(rows)


def universe_at(conn: sqlite3.Connection, date: str) -> list[dict]:
    """查询历史时点快照（<= date 的最近一次）；无快照返回空表。"""
    row = conn.execute(
        "SELECT MAX(date) d FROM stock_universe_history WHERE date <= ?", (date,)
    ).fetchone()
    if not row or not row["d"]:
        return []
    rows = conn.execute(
        "SELECT symbol, industry, is_st, src FROM stock_universe_history WHERE date=? ORDER BY symbol",
        (row["d"],),
    ).fetchall()
    out = [dict(r) for r in rows]
    for item in out:
        item["as_of"] = row["d"]
    return out


def industry_at(conn: sqlite3.Connection, symbol: str, date: str) -> str:
    """查询某标的历史时点行业（<= date 最近快照）；无记录返回空串。"""
    rows = universe_at(conn, date)
    for r in rows:
        if r["symbol"] == symbol:
            return r["industry"]
    return ""


def st_at(conn: sqlite3.Connection, symbol: str, date: str) -> bool:
    """查询某标的历史时点 ST 状态；无记录返回 False。"""
    rows = universe_at(conn, date)
    for r in rows:
        if r["symbol"] == symbol:
            return bool(r["is_st"])
    return False
