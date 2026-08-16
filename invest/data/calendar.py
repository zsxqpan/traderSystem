"""交易日历与事件日历。

v1 用"工作日近似"判定交易日；接入交易所日历后替换 is_trading_day。
"""
from __future__ import annotations

import datetime as dt
import sqlite3


def is_trading_day(d: dt.date) -> bool:
    """近似交易日：周一至周五。节假日处理后续接入官方日历。"""
    return d.weekday() < 5


def get_trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """返回 [start, end] 区间内的近似交易日。"""
    days: list[dt.date] = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def add_event(
    conn: sqlite3.Connection,
    date: dt.date,
    kind: str,
    title: str = "",
    target: str = "",
    level: str = "normal",
) -> None:
    """向事件日历登记一条事件。"""
    with conn:
        conn.execute(
            "INSERT INTO event_calendar(date, kind, title, target, level, created_at) VALUES(?,?,?,?,?, datetime('now','localtime'))",
            (date.isoformat(), kind, title, target, level),
        )


def upcoming_events(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """返回未来 days 天内的日历事件（按日期升序）。"""
    today = dt.date.today().isoformat()
    limit = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT date, kind, title, target, level FROM event_calendar
           WHERE date BETWEEN ? AND ? ORDER BY date""",
        (today, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_trading_day(d: dt.date | None = None) -> dt.date:
    """最近一个交易日（含今天；今天非交易日则回退到上一个工作日）。"""
    d = d or dt.date.today()
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d
