"""扫描宇宙：监控层（core/track + 持仓卡）+ 热门板块核心（昨涨停按行业）。"""
from __future__ import annotations

import datetime as dt
import sqlite3

from invest.signals.bars import compact


def watch_symbols(conn: sqlite3.Connection) -> list[str]:
    """候选池 core/track + 持仓卡片 locked/review，去重保序。"""
    syms: list[str] = []
    try:
        for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level IN ('core','track') "
            "AND out_date IS NULL"
        ):
            if r["symbol"] not in syms:
                syms.append(r["symbol"])
        for r in conn.execute(
            "SELECT symbol FROM cards WHERE status IN ('locked','review')"
        ):
            if r["symbol"] not in syms:
                syms.append(r["symbol"])
    except Exception:
        pass
    return syms


def _latest_zt_date(conn: sqlite3.Connection, asof: dt.date) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) AS d FROM limit_up_pool WHERE REPLACE(date,'-','') < ?",
        (compact(asof),),
    ).fetchone()
    return row["d"] if row and row["d"] else None


def yesterday_zt(conn: sqlite3.Connection, asof: dt.date) -> list[dict]:
    """asof 之前最近一个涨停池日的非炸板记录。"""
    d = _latest_zt_date(conn, asof)
    if not d:
        return []
    try:
        rows = conn.execute(
            """SELECT symbol, name, lianban FROM limit_up_pool
               WHERE date=? AND (zhaban=0 OR zhaban IS NULL)
               ORDER BY lianban DESC""",
            (d,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def lianban_map(conn: sqlite3.Connection, asof: dt.date) -> dict[str, int]:
    return {r["symbol"]: int(r["lianban"] or 0) for r in yesterday_zt(conn, asof)}


def hot_sector_cores(
    conn: sqlite3.Connection,
    asof: dt.date | None = None,
    per_block: int = 3,
    n_blocks: int = 3,
) -> list[dict]:
    """昨涨停按东财行业聚合 TOP 板块，每板块成交量最大的 per_block 只。

    返回 [{block, count, stocks: [{symbol, name, lianban, volume}]}]。
    """
    asof = asof or dt.date.today()
    rows = yesterday_zt(conn, asof)
    if not rows:
        return []
    vol_map: dict[str, float] = {}
    for r in rows:
        try:
            row = conn.execute(
                "SELECT volume FROM daily_bars WHERE symbol=? "
                "AND REPLACE(date,'-','') < ? "
                "ORDER BY REPLACE(date,'-','') DESC LIMIT 1",
                (r["symbol"], compact(asof)),
            ).fetchone()
            vol_map[r["symbol"]] = float(row["volume"]) if row and row["volume"] else 0.0
        except Exception:
            vol_map[r["symbol"]] = 0.0
    try:
        from invest.data.auction import fetch_industries

        ind_map = fetch_industries([r["symbol"] for r in rows]) or {}
    except Exception:
        ind_map = {}
    blocks: dict[str, list] = {}
    for r in rows:
        ind = ind_map.get(r["symbol"]) or "其他"
        item = dict(r)
        item["volume"] = vol_map.get(r["symbol"], 0.0)
        blocks.setdefault(ind, []).append(item)
    top = sorted(blocks.items(), key=lambda kv: -len(kv[1]))[:n_blocks]
    out = []
    for ind, stocks in top:
        stocks.sort(key=lambda s: -s.get("volume", 0.0))
        out.append({"block": ind, "count": len(stocks), "stocks": stocks[:per_block]})
    return out
