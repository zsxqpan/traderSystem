"""交易信号编排：一次计算，报告/落库/比价 overlay 共用。"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3

from invest.signals.bars import bar_stats
from invest.signals.format import pick_signals
from invest.signals.persist import persist_auction_snapshots, persist_signals
from invest.signals.rules import (
    auction_height_signals,
    auction_volume_signals,
    etf_signals,
    sector_signals,
    shrink_highvol_signals,
    space_signals,
)
from invest.signals.thresholds import DISPLAY_LIMIT
from invest.signals.types import Signal
from invest.signals.universe import hot_sector_cores, lianban_map, watch_symbols, yesterday_zt

logger = logging.getLogger(__name__)


def _fetch_quotes(symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    try:
        from invest.data.auction import fetch_batch_quotes

        return fetch_batch_quotes(symbols) or {}
    except Exception as exc:
        logger.warning("信号行情获取失败: %s", exc)
        return {}


def scan(
    conn: sqlite3.Connection,
    session: str,
    asof: dt.date | None = None,
    now: dt.datetime | None = None,
    quotes: dict[str, dict] | None = None,
    etf_quotes: dict[str, dict] | None = None,
    persist: bool = False,
    limit: int = DISPLAY_LIMIT,
) -> list[Signal]:
    """计算当前会话信号。quotes/etf_quotes 注入则不联网；失败返回已算出的子集。"""
    asof = asof or dt.date.today()
    now = now or dt.datetime.now()
    watch = watch_symbols(conn)
    zt = yesterday_zt(conn, asof)
    hot = hot_sector_cores(conn, asof)
    lb = lianban_map(conn, asof)
    hot_syms = [s["symbol"] for b in hot for s in (b.get("stocks") or [])]
    need = list(dict.fromkeys(watch + [r["symbol"] for r in zt] + hot_syms))
    stats = {}
    for sym in need:
        try:
            st = bar_stats(conn, sym, asof)
            if st:
                stats[sym] = st
        except Exception:
            pass
    if quotes is None and session in ("auction", "intraday"):
        quotes = _fetch_quotes(need)
    quotes = quotes or {}

    out: list[Signal] = []
    try:
        if session == "auction":
            out.extend(auction_volume_signals(session, watch, zt, quotes, stats, lb))
            out.extend(auction_height_signals(session, zt, quotes))
        if session in ("intraday", "close"):
            out.extend(shrink_highvol_signals(session, now, watch, quotes, stats, lb))
            out.extend(sector_signals(session, now, hot, quotes, stats, lb))
            out.extend(space_signals(conn, session, asof))
        if etf_quotes is not None:
            out.extend(etf_signals(session, etf_quotes))
    except Exception as exc:
        logger.warning("信号规则执行失败: %s", exc)

    picked = pick_signals(out, limit)
    if persist:
        try:
            persist_signals(conn, out, asof, session)
            if session == "auction":
                persist_auction_snapshots(conn, quotes, asof)
        except Exception as exc:
            logger.warning("信号落库失败: %s", exc)
    return picked


def scan_db(db_path: str, session: str, **kwargs) -> list[Signal]:
    """打开库跑 scan，失败返回 []（报告用不阻断）。"""
    from invest.db import connect

    try:
        conn = connect(db_path)
        try:
            return scan(conn, session, **kwargs)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("信号扫描失败: %s", exc)
        return []
