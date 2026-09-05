"""报告数据快照：一次冻结，指数/ETF/核心池共享 as_of。"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from invest.db import connect


@dataclass
class DataBlock:
    """一个数据块：时点、是否实时、载荷与可选 QuoteResult。"""

    name: str
    as_of: str
    realtime: bool
    payload: Any = None
    quotes: list | None = None


@dataclass
class ReportSnapshot:
    """一次冻结的报告输入。"""

    skill_id: str
    as_of: str
    blocks: dict[str, DataBlock] = field(default_factory=dict)


def _iso(now: dt.datetime) -> str:
    return now.replace(microsecond=0).isoformat(timespec="seconds")


def _core_symbols(db_path: str) -> list[str]:
    conn = connect(db_path)
    try:
        syms = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level IN ('core','track') "
            "AND out_date IS NULL"
        ).fetchall()]
        try:
            syms += [r["symbol"] for r in conn.execute(
                "SELECT symbol FROM cards WHERE status IN ('locked','review')"
            ).fetchall()]
        except Exception:
            pass
        return list(dict.fromkeys(syms))
    finally:
        conn.close()


def _sector_eod_payload(db_path: str) -> dict[str, str]:
    """板块 EOD：直接查库，避免与 b1_intraday 循环 import。"""
    conn = connect(db_path)
    try:
        try:
            rows = conn.execute(
                """SELECT t.industry, t.close, p.close AS prev
                   FROM industry_bars t
                   JOIN industry_bars p ON p.industry = t.industry
                     AND p.date = (SELECT MAX(date) FROM industry_bars
                                   WHERE industry=t.industry AND date < t.date)
                   WHERE t.date = (SELECT MAX(date) FROM industry_bars)
                     AND t.close IS NOT NULL AND p.close IS NOT NULL AND p.close > 0
                   ORDER BY (t.close/p.close - 1) DESC LIMIT 5"""
            ).fetchall()
            sector_top = "\n".join(
                f"  {r['industry']} {(r['close']/r['prev']-1):+.2%}" for r in rows
            ) or ""
        except Exception:
            sector_top = ""
        try:
            rows = conn.execute(
                """SELECT industry, main_net FROM sector_fund_flow
                   WHERE date=(SELECT MAX(date) FROM sector_fund_flow)
                   ORDER BY main_net DESC LIMIT 5"""
            ).fetchall()
            fund_top = "\n".join(
                f"  {r['industry']} 主力净流入{float(r['main_net'])/1e8:+.2f}亿"
                for r in rows
            ) or ""
        except Exception:
            fund_top = ""
        return {"sector_top": sector_top, "fund_top": fund_top}
    finally:
        conn.close()


def _freeze_b1(db_path: str, now: dt.datetime) -> ReportSnapshot:
    from invest.data.etf import INDEX_ETFS, SECTOR_ETFS
    from invest.data.quotes import INDEX_UNIVERSE, get_quotes

    as_of = _iso(now)
    snap = ReportSnapshot(skill_id="b1_intraday", as_of=as_of)
    idx = get_quotes(list(INDEX_UNIVERSE), obj_type="index")
    snap.blocks["index_quotes"] = DataBlock(
        "index_quotes", as_of, True, payload=idx, quotes=idx,
    )
    etf_codes = list(INDEX_ETFS) + [c for v in SECTOR_ETFS.values() for c in v]
    try:
        etf = get_quotes(etf_codes, obj_type="etf")
    except Exception:
        etf = []
    snap.blocks["etf_quotes"] = DataBlock(
        "etf_quotes", as_of, True, payload=etf, quotes=etf,
    )
    core_syms = _core_symbols(db_path)
    core = get_quotes(core_syms, obj_type="stock", db_path=db_path) if core_syms else []
    snap.blocks["core_quotes"] = DataBlock(
        "core_quotes", as_of, True, payload=core, quotes=core,
    )
    eod = _sector_eod_payload(db_path)
    snap.blocks["sector_eod"] = DataBlock(
        "sector_eod", as_of, False, payload=eod,
    )
    return snap


def _freeze_a7(db_path: str, now: dt.datetime) -> ReportSnapshot:
    from invest.data.auction import fetch_top_gainers, fetch_top_losers, fetch_vol_top
    from invest.data.quotes import INDEX_UNIVERSE, get_quotes
    from invest.skills.reports import a7_auction

    if dt.time(9, 25) <= now.time() <= dt.time(9, 30):
        as_of = _iso(now.replace(hour=9, minute=25, second=0, microsecond=0))
    else:
        as_of = _iso(now)
    snap = ReportSnapshot(skill_id="a7_auction", as_of=as_of)
    idx = get_quotes(list(INDEX_UNIVERSE), obj_type="index")
    snap.blocks["index_quotes"] = DataBlock(
        "index_quotes", as_of, True, payload=idx, quotes=idx,
    )
    try:
        boards = {
            "gainers": fetch_top_gainers(8),
            "losers": fetch_top_losers(3),
            "vol_top": fetch_vol_top(8),
        }
    except Exception:
        boards = {"gainers": [], "losers": [], "vol_top": []}
    snap.blocks["auction_boards"] = DataBlock(
        "auction_boards", as_of, True, payload=boards,
    )
    conn = connect(db_path)
    try:
        ladder_syms = a7_auction._yesterday_ladder(conn)
        hot = a7_auction._hot_core_stocks(conn)
    finally:
        conn.close()
    key_syms = [s["symbol"] for b in hot for s in b.get("stocks") or []]
    core_syms = a7_auction._core_symbols(db_path)
    if ladder_syms:
        ladder = get_quotes(ladder_syms, obj_type="stock", db_path=db_path)
    else:
        ladder = []
    snap.blocks["ladder_quotes"] = DataBlock(
        "ladder_quotes", as_of, True, payload=ladder, quotes=ladder,
    )
    if key_syms:
        key = get_quotes(key_syms, obj_type="stock", db_path=db_path)
    else:
        key = []
    snap.blocks["key_quotes"] = DataBlock(
        "key_quotes", as_of, True, payload={"hot": hot, "quotes": key}, quotes=key,
    )
    if core_syms:
        core = get_quotes(core_syms, obj_type="stock", db_path=db_path)
    else:
        core = []
    snap.blocks["core_quotes"] = DataBlock(
        "core_quotes", as_of, True, payload=core, quotes=core,
    )
    return snap


def _freeze_light(skill_id: str, now: dt.datetime) -> ReportSnapshot:
    """盘前/晚报：只钉 as_of，不种空列表冒充块。这些报告暂不走 deliver_report 门禁。"""
    as_of = _iso(now)
    snap = ReportSnapshot(skill_id=skill_id, as_of=as_of)
    snap.blocks["freshness"] = DataBlock("freshness", as_of, False, payload="ok")
    return snap


def freeze_snapshot(
    skill_id: str,
    db_path: str,
    *,
    now: dt.datetime | None = None,
) -> ReportSnapshot:
    """按报告 id 一次冻结输入；竞价钉 9:25，盘中指数/ETF/核心共享 as_of。"""
    now = now or dt.datetime.now()
    if skill_id == "b1_intraday":
        return _freeze_b1(db_path, now)
    if skill_id == "a7_auction":
        return _freeze_a7(db_path, now)
    return _freeze_light(skill_id, now)
