"""交易信号编排：一次计算，报告/落库/比价 overlay 共用。"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3

from invest.signals.bars import bar_stats, compact
from invest.signals.format import pick_signals
from invest.signals.persist import persist_auction_snapshots, persist_signals
from invest.signals.rules import (
    auction_height_signals,
    auction_keep_vol_yoy_signals,
    auction_volume_signals,
    board_open_vol_signals,
    etf_signals,
    lianban_ladder_signals,
    rs_industry_leader_signals,
    sector_flow_spike_signals,
    sector_signals,
    shrink_highvol_signals,
    space_signals,
)
from invest.signals.thresholds import DISPLAY_LIMIT
from invest.signals.types import Signal
from invest.signals.universe import (
    assign_layer,
    discovery_symbols,
    hot_sector_cores,
    lianban_map,
    watch_symbols,
    yesterday_zt,
)

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


def _tag_short(signals: list[Signal], watch: list[str], discovery: set[str]) -> None:
    """短线规则：horizon=short；layer 用宇宙推断，显式 market/discovery 保留。"""
    watch_set = set(watch)
    for s in signals:
        s.horizon = "short"
        if s.layer in ("market", "discovery"):
            continue
        s.layer = assign_layer(s.subject_type, s.subject, watch_set, discovery)


def _auction_yoy_vols(conn: sqlite3.Connection, asof: dt.date) -> dict[str, float]:
    try:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM auction_snapshots WHERE REPLACE(date,'-','') < ?",
            (compact(asof),),
        ).fetchone()
        if not row or not row["d"]:
            return {}
        return {
            r["symbol"]: float(r["vol"] or 0)
            for r in conn.execute(
                "SELECT symbol, vol FROM auction_snapshots WHERE date=?", (row["d"],)
            )
            if r["symbol"]
        }
    except Exception:
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
    horizon: str | None = None,
    layers: list[str] | None = None,
    boards: list[dict] | None = None,
) -> list[Signal]:
    """计算当前会话信号。quotes/etf_quotes 注入则不联网；失败返回已算出的子集。

    horizon/layers 为 None 时不过滤。boards 仅 auction 使用，None 时不高开榜联网。
    session=daily 跑中线 + C7/C9 短线日更规则，禁止拉行情。
    """
    asof = asof or dt.date.today()
    now = now or dt.datetime.now()

    if session == "daily":
        out: list[Signal] = []
        mid_ok = True
        try:
            from invest.signals.mid import mid_signals

            out = mid_signals(conn)
        except Exception as exc:
            mid_ok = False
            logger.warning("中线规则执行失败: %s", exc)
        try:
            out.extend(sector_flow_spike_signals(conn))
            out.extend(rs_industry_leader_signals(conn))
        except Exception as exc:
            logger.warning("日更短线规则执行失败: %s", exc)
        picked = pick_signals(out, limit, layers=layers, horizon=horizon)
        # mid 整体失败时不要 DELETE+INSERT，以免抹掉当日已落库的四象限。
        if persist and mid_ok:
            try:
                persist_signals(conn, out, asof, session)
            except Exception as exc:
                logger.warning("信号落库失败: %s", exc)
        return picked

    watch = watch_symbols(conn)
    zt = yesterday_zt(conn, asof)
    hot = hot_sector_cores(conn, asof)
    lb = lianban_map(conn, asof)
    disc_list = discovery_symbols(conn, asof, boards=boards, hot=hot)
    discovery = set(disc_list)
    need = list(dict.fromkeys(watch + disc_list))
    scan_syms = need
    stats: dict[str, dict] = {}
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

    out = []
    try:
        if session == "auction":
            out.extend(auction_volume_signals(session, watch, zt, quotes, stats, lb))
            out.extend(auction_height_signals(session, zt, quotes))
            out.extend(auction_keep_vol_yoy_signals(
                session, watch, zt, quotes, _auction_yoy_vols(conn, asof),
            ))
            out.extend(board_open_vol_signals(session, boards))
        if session in ("intraday", "close"):
            out.extend(shrink_highvol_signals(session, now, scan_syms, quotes, stats, lb))
            out.extend(sector_signals(session, now, hot, quotes, stats, lb))
            out.extend(space_signals(conn, session, asof))
            if session == "close":
                out.extend(lianban_ladder_signals(conn, session, asof))
        if etf_quotes is not None:
            out.extend(etf_signals(session, etf_quotes))
    except Exception as exc:
        logger.warning("信号规则执行失败: %s", exc)

    _tag_short(out, watch, discovery)

    picked = pick_signals(out, limit, layers=layers, horizon=horizon)
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
