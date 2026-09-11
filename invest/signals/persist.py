"""trade_signals / auction_snapshots 落库。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from invest.signals.bars import iso
from invest.signals.types import Signal


def persist_signals(
    conn: sqlite3.Connection,
    signals: list[Signal],
    asof: date,
    session: str,
) -> None:
    day = iso(asof)
    conn.execute("DELETE FROM trade_signals WHERE date=? AND session=?", (day, session))
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(trade_signals)")}
    has_hl = "horizon" in cols and "layer" in cols
    for s in signals:
        evidence = json.dumps(s.evidence or {}, ensure_ascii=False)
        if has_hl:
            conn.execute(
                """INSERT OR REPLACE INTO trade_signals
                   (date, session, signal_id, subject_type, subject, severity, name, hint, evidence, src, horizon, layer)
                   VALUES (?,?,?,?,?,?,?,?,?, 'signals', ?, ?)""",
                (day, session, s.id, s.subject_type, s.subject, s.severity,
                 s.name, s.hint, evidence,
                 getattr(s, "horizon", None) or "short",
                 getattr(s, "layer", None) or "watch"),
            )
        else:
            conn.execute(
                """INSERT OR REPLACE INTO trade_signals
                   (date, session, signal_id, subject_type, subject, severity, name, hint, evidence, src)
                   VALUES (?,?,?,?,?,?,?,?,?, 'signals')""",
                (day, session, s.id, s.subject_type, s.subject, s.severity,
                 s.name, s.hint, evidence),
            )
    conn.commit()


def persist_auction_snapshots(
    conn: sqlite3.Connection,
    quotes: dict[str, dict],
    asof: date,
) -> None:
    day = iso(asof)
    conn.execute("DELETE FROM auction_snapshots WHERE date=?", (day,))
    for sym, q in (quotes or {}).items():
        if not q:
            continue
        price = q.get("price")
        vol = q.get("vol")
        amount = q.get("amount")
        if amount is None and price is not None and vol is not None:
            try:
                amount = float(price) * float(vol) * 100.0
            except (TypeError, ValueError):
                amount = None
        conn.execute(
            """INSERT OR REPLACE INTO auction_snapshots
               (date, symbol, name, price, pct, vol, amount, src)
               VALUES (?,?,?,?,?,?,?, 'tencent')""",
            (day, sym, q.get("name"), price, q.get("pct"), vol, amount),
        )
    conn.commit()
