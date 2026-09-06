"""D32 交易信号 skill（竞价保量/缩量/高位放量/板块集体/空间高度广度）。"""
from __future__ import annotations

SKILL = {
    "id": "d32_trade_signals",
    "name": "交易信号",
    "kind": "section",
    "description": "短线交易信号（竞价保量/缩量分歧/极致缩量/高位放量/板块集体/空间高度广度）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "session": "str, optional, default intraday（auction/intraday/close）",
    },
}


def render(db_path: str, session: str = "intraday") -> str:
    from invest.db import connect
    from invest.signals.format import format_signals
    from invest.signals.scan import scan

    if session not in ("auction", "intraday", "close"):
        session = "intraday"
    conn = connect(db_path)
    try:
        return format_signals(scan(conn, session))
    except Exception:
        return ""
    finally:
        conn.close()
