"""观察名单：半自动漏斗，不自动入 candidate_pool core。"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3

from invest.actions.compose import valid_symbol

logger = logging.getLogger(__name__)


def _iso(d) -> str:
    if hasattr(d, "isoformat"):
        return d.isoformat()[:10]
    return str(d)[:10]


def _plus_trading_days(start: dt.date, n: int) -> dt.date:
    from invest.data.calendar import is_trading_day

    d = start
    got = 0
    while got < n:
        d += dt.timedelta(days=1)
        if is_trading_day(d):
            got += 1
    return d


def _prev_trading_day(d: dt.date) -> dt.date | None:
    from invest.data.calendar import is_trading_day

    for i in range(1, 8):
        cand = d - dt.timedelta(days=i)
        if is_trading_day(cand):
            return cand
    return None


def add_watch(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    source: str = "user",
    source_ref: str = "",
    reason: str = "",
    asof: dt.date | str | None = None,
) -> dict:
    sym = valid_symbol(symbol)
    if not sym:
        raise ValueError("观察名单需要 6 位代码")
    if source not in ("signal", "llm_pick", "user"):
        raise ValueError("source 必须为 signal/llm_pick/user")
    day = dt.date.fromisoformat(_iso(asof or dt.date.today()))
    existing = conn.execute(
        "SELECT id FROM watch_items WHERE symbol=? AND status='open'", (sym,)
    ).fetchone()
    if existing:
        return {"symbol": sym, "status": "open", "id": existing["id"]}
    exp = _plus_trading_days(day, 5).isoformat()
    try:
        cur = conn.execute(
            """INSERT INTO watch_items(symbol, source, source_ref, reason, status, in_date, expire_date)
               VALUES (?,?,?,?, 'open', ?, ?)""",
            (sym, source, source_ref, reason, day.isoformat(), exp),
        )
        conn.commit()
        return {"symbol": sym, "status": "open", "id": cur.lastrowid}
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT id FROM watch_items WHERE symbol=? AND status='open'", (sym,)
        ).fetchone()
        return {"symbol": sym, "status": "open", "id": row["id"] if row else None}


def list_watch(conn: sqlite3.Connection, status: str = "open") -> list[dict]:
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(watch_items)")}
        if not cols:
            return []
        rows = conn.execute(
            "SELECT * FROM watch_items WHERE status=? ORDER BY in_date, symbol",
            (status,),
        )
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("list_watch 失败: %s", exc)
        return []


def expire_due(conn: sqlite3.Connection, asof: dt.date | str) -> int:
    """把 expire_date 已到的 open 行标 expired。"""
    day = _iso(asof)
    try:
        cur = conn.execute(
            """UPDATE watch_items SET status='expired'
               WHERE status='open' AND expire_date IS NOT NULL AND expire_date<=?""",
            (day,),
        )
        if cur.rowcount:
            conn.commit()
        return int(cur.rowcount or 0)
    except Exception as exc:
        logger.warning("expire_due 失败: %s", exc)
        return 0


def dismiss_watch(conn: sqlite3.Connection, symbol: str) -> None:
    sym = valid_symbol(symbol) or (symbol or "").strip()
    cur = conn.execute(
        "UPDATE watch_items SET status='dismissed' WHERE symbol=? AND status='open'",
        (sym,),
    )
    if cur.rowcount == 0:
        raise ValueError(f"{sym} 不在观察名单")
    conn.commit()
    try:
        from invest.data.pit import record_decision

        record_decision(conn, decision="reject", symbol=sym, reason="观察名单驳回")
    except Exception:
        pass


def promote_watch(conn: sqlite3.Connection, symbol: str, level: str = "track") -> dict:
    from invest.discipline.pool import add_to_pool

    if level not in ("track", "core", "rest"):
        raise ValueError("level 必须为 track/core/rest")
    sym = valid_symbol(symbol) or (symbol or "").strip()
    row = conn.execute(
        "SELECT * FROM watch_items WHERE symbol=? AND status='open'", (sym,)
    ).fetchone()
    if row is None:
        raise ValueError(f"{sym} 不在观察名单")
    out = add_to_pool(conn, sym, level=level, reason=row["reason"] or "观察名单升级")
    conn.execute(
        "UPDATE watch_items SET status='promoted' WHERE symbol=? AND status='open'",
        (sym,),
    )
    conn.commit()
    return out


def maybe_open_from_actions(conn: sqlite3.Connection, asof: dt.date | str) -> list[str]:
    """连续 2 个交易日 verb=watch 且 source=signal → 进观察名单，不入池。"""
    day = dt.date.fromisoformat(_iso(asof))
    prev = _prev_trading_day(day)
    if prev is None:
        return []
    try:
        today = {
            r["symbol"] for r in conn.execute(
                "SELECT symbol FROM daily_actions WHERE date=? AND verb='watch' AND source='signal'",
                (day.isoformat(),),
            )
        }
        yday = {
            r["symbol"] for r in conn.execute(
                "SELECT symbol FROM daily_actions WHERE date=? AND verb='watch' AND source='signal'",
                (prev.isoformat(),),
            )
        }
    except Exception:
        return []
    opened: list[str] = []
    for sym in sorted(today & yday):
        try:
            add_watch(conn, sym, source="signal", source_ref=day.isoformat(),
                      reason="连续2日信号观察", asof=day)
            opened.append(sym)
        except Exception as exc:
            logger.warning("maybe_open %s 失败: %s", sym, exc)
            continue
    return opened
