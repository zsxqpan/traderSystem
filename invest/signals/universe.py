"""扫描宇宙：监控层（core/track + 持仓卡）+ 热门板块核心（昨涨停按行业）。"""
from __future__ import annotations

import datetime as dt
import sqlite3

from invest.signals.bars import compact
from invest.signals.thresholds import (
    DISCOVERY_STOCK_CAP,
    LHB_DISCOVERY_CAP,
    RS_CORE_PER_INDUSTRY,
    RS_INDUSTRY_TOP,
)


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


def assign_layer(
    subject_type: str,
    subject: str,
    watch: set[str],
    discovery: set[str],
) -> str:
    """按标的类型与宇宙推断 layer：market / watch / discovery。"""
    if subject_type in ("market", "etf"):
        return "market"
    if subject_type == "sector":
        return "discovery"
    if subject in watch:
        return "watch"
    if subject in discovery:
        return "discovery"
    return "discovery"


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
    from invest.data.industry_map import industry_of, load_industry_stocks

    mapping = load_industry_stocks()
    try:
        from invest.data.auction import fetch_industries

        ind_map = fetch_industries([r["symbol"] for r in rows]) or {}
    except Exception:
        ind_map = {}
    blocks: dict[str, list] = {}
    for r in rows:
        # 未知行业不得并进「其他」：否则无关昨涨停会被当成同一板块集体放量。
        ind = industry_of(conn, r["symbol"], mapping) or ind_map.get(r["symbol"]) or ""
        ind = str(ind).strip()
        if not ind or ind == "其他":
            continue
        item = dict(r)
        item["volume"] = vol_map.get(r["symbol"], 0.0)
        blocks.setdefault(ind, []).append(item)
    top = sorted(blocks.items(), key=lambda kv: -len(kv[1]))[:n_blocks]
    out = []
    for ind, stocks in top:
        stocks.sort(key=lambda s: -s.get("volume", 0.0))
        out.append({"block": ind, "count": len(stocks), "stocks": stocks[:per_block]})
    return out


def discovery_cap_ok(symbols) -> None:
    """发现层股票上限断言（测试/自检用）。"""
    assert len(list(symbols)) <= DISCOVERY_STOCK_CAP


def lhb_symbols(conn: sqlite3.Connection, cap: int = LHB_DISCOVERY_CAP) -> list[str]:
    """最新龙虎日 DISTINCT symbol，上限 cap。无表/空返回 []。"""
    try:
        row = conn.execute("SELECT MAX(date) AS d FROM dragon_tiger").fetchone()
        if not row or not row["d"]:
            return []
        rows = conn.execute(
            """SELECT symbol, MAX(ABS(COALESCE(net, 0))) AS mag
               FROM dragon_tiger WHERE date=?
               GROUP BY symbol ORDER BY mag DESC LIMIT ?""",
            (row["d"], cap),
        ).fetchall()
        return [r["symbol"] for r in rows if r["symbol"]]
    except Exception:
        return []


def rs_core_symbols(
    conn: sqlite3.Connection,
    asof: dt.date,
    mapping: dict | None = None,
    per_industry: int = RS_CORE_PER_INDUSTRY,
) -> list[str]:
    """短线 RS TOP 行业的映射内核心股，按最近一日 amount 取 per_industry 只。

    映射为空的行业跳过，不退回全市场 daily_bars。
    """
    from invest.data.industry_map import load_industry_stocks, stocks_of

    m = mapping if mapping is not None else load_industry_stocks()
    try:
        latest = conn.execute(
            """SELECT MAX(run_date) AS d FROM quant_strength
               WHERE period='short' AND obj_type='industry'"""
        ).fetchone()["d"]
    except Exception:
        return []
    if not latest:
        return []
    try:
        industries = conn.execute(
            """SELECT obj FROM quant_strength
               WHERE period='short' AND obj_type='industry' AND run_date=?
               ORDER BY rs DESC LIMIT ?""",
            (latest, RS_INDUSTRY_TOP),
        ).fetchall()
    except Exception:
        return []
    cut = compact(asof)
    out: list[str] = []
    for r in industries:
        ind = r["obj"]
        mapped = stocks_of(ind, m)
        if not mapped:
            continue
        pool_syms: list[str] = []
        try:
            pool_syms = [
                str(x["symbol"])
                for x in conn.execute(
                    "SELECT symbol FROM candidate_pool "
                    "WHERE industry=? AND out_date IS NULL",
                    (ind,),
                )
            ]
        except Exception:
            pass
        cands = list(dict.fromkeys(list(mapped) + pool_syms))
        scored: list[tuple[float, str]] = []
        for sym in cands:
            try:
                row = conn.execute(
                    """SELECT amount, volume FROM daily_bars
                       WHERE symbol=? AND REPLACE(date,'-','') <= ?
                       ORDER BY REPLACE(date,'-','') DESC LIMIT 1""",
                    (sym, cut),
                ).fetchone()
            except Exception:
                row = None
            amt = 0.0
            if row:
                raw = row["amount"] if row["amount"] is not None else row["volume"]
                try:
                    amt = float(raw or 0)
                except (TypeError, ValueError):
                    amt = 0.0
            scored.append((amt, sym))
        scored.sort(key=lambda kv: -kv[0])
        for _, sym in scored[:per_industry]:
            if sym not in out:
                out.append(sym)
    return out


def discovery_symbols(
    conn: sqlite3.Connection,
    asof: dt.date,
    *,
    boards: list[dict] | None = None,
    cap: int = DISCOVERY_STOCK_CAP,
    hot: list[dict] | None = None,
) -> list[str]:
    """昨涨停 ∪ 热门板块核心 ∪ boards ∪ RS 核心 ∪ 龙虎。去重，截断 cap。

    RS 核心与龙虎优先保留：涨停日 ≥cap 时不能把发现宇宙截成纯涨停基因。
    hot 传入则不再重算热门核心（scan 已算过一遍）。
    """
    must: list[str] = []
    filler: list[str] = []
    seen: set[str] = set()

    def _add(dest: list[str], sym: str | None) -> None:
        if not sym or sym in seen:
            return
        seen.add(sym)
        dest.append(sym)

    for s in rs_core_symbols(conn, asof):
        _add(must, s)
    for s in lhb_symbols(conn):
        _add(must, s)
    if len(must) > cap:
        must = must[:cap]
        return must

    for r in yesterday_zt(conn, asof):
        _add(filler, r.get("symbol"))
    for block in (hot if hot is not None else hot_sector_cores(conn, asof)):
        for s in block.get("stocks") or []:
            _add(filler, s.get("symbol"))
    for b in boards or []:
        if isinstance(b, dict):
            _add(filler, b.get("symbol"))
    room = cap - len(must)
    return must + filler[:room]


def quote_symbols(
    conn: sqlite3.Connection,
    asof: dt.date,
    boards: list[dict] | None = None,
) -> list[str]:
    """watch ∪ discovery，盘中/竞价批量行情覆盖范围。"""
    return list(dict.fromkeys(watch_symbols(conn) + discovery_symbols(conn, asof, boards=boards)))
