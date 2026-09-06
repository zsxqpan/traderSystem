"""日线统计：昨量、5 日均量、20 日高、近 5 日涨幅。"""
from __future__ import annotations

import datetime as dt
import sqlite3


def compact(d: dt.date | str) -> str:
    if isinstance(d, dt.date):
        return d.strftime("%Y%m%d")
    s = str(d).replace("-", "")
    return s[:8]


def iso(d: dt.date | str) -> str:
    if isinstance(d, dt.date):
        return d.isoformat()
    s = str(d)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def is_20cm(symbol: str) -> bool:
    s = (symbol or "").strip()
    if s.lower().startswith(("sh", "sz", "bj")):
        s = s[2:]
    return s.startswith(("300", "301", "688", "689"))


def bar_stats(conn: sqlite3.Connection, symbol: str, asof: dt.date) -> dict | None:
    """asof 当日之前为历史；asof 当日行为 today（收盘会话用）。"""
    cut = compact(asof)
    rows = conn.execute(
        """SELECT date, open, high, low, close, volume FROM daily_bars
           WHERE symbol=? AND REPLACE(date,'-','') <= ?
           ORDER BY REPLACE(date,'-','') DESC LIMIT 25""",
        (symbol, cut),
    ).fetchall()
    if not rows:
        return None
    bars = [dict(r) for r in reversed(rows)]
    today = None
    hist: list[dict] = []
    for b in bars:
        if compact(b["date"]) == cut:
            today = b
        else:
            hist.append(b)
    if not hist:
        return None
    yday = hist[-1]
    prev5 = hist[-5:]
    vols = [float(b["volume"] or 0) for b in prev5 if b.get("volume")]
    avg5 = (sum(vols) / len(vols)) if vols else 0.0
    highs = [float(b["high"] or b["close"] or 0) for b in hist[-20:]]
    high20 = max(highs) if highs else 0.0
    prev_close = float(yday["close"] or 0)
    ret5 = None
    if len(hist) >= 6 and hist[-6].get("close"):
        base = float(hist[-6]["close"] or 0)
        if base > 0:
            ret5 = prev_close / base - 1.0
    today_vol = float(today["volume"] or 0) if today and today.get("volume") else None
    today_close = float(today["close"] or 0) if today and today.get("close") else None
    today_high = float(today["high"] or 0) if today and today.get("high") else None
    return {
        "yday_vol": float(yday["volume"] or 0),
        "avg5": avg5,
        "prev_close": prev_close,
        "high20": high20,
        "ret5": ret5,
        "today_vol": today_vol,
        "today_close": today_close,
        "today_high": today_high,
    }
