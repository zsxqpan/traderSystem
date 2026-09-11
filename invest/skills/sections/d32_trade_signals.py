"""D32 交易信号 skill（竞价保量/缩量/高位放量/板块集体/空间高度广度；可按 horizon/layer 过滤）。"""
from __future__ import annotations

SKILL = {
    "id": "d32_trade_signals",
    "name": "交易信号",
    "kind": "section",
    "description": "交易信号（session=auction/intraday/close/daily；horizon=short/mid；layer=watch/discovery/market）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "session": "str, optional, default intraday（auction/intraday/close/daily）",
        "horizon": "str, optional, short/mid，空=不过滤",
        "layer": "str, optional, watch/discovery/market，空=不过滤",
    },
}


def _blank(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _rows_to_signals(rows: list[dict]):
    from invest.signals.types import Signal

    out = []
    for r in rows:
        out.append(Signal(
            id=r.get("signal_id") or "",
            name=r.get("name") or "",
            session=r.get("session") or "daily",
            severity=r.get("severity") or "info",
            subject_type=r.get("subject_type") or "stock",
            subject=r.get("subject") or "",
            hint=r.get("hint") or "",
            evidence={},
            horizon=r.get("horizon") or "short",
            layer=r.get("layer") or "watch",
        ))
    return out


def render(db_path: str, session: str = "intraday", horizon: str = "", layer: str = "") -> str:
    """只读：scan persist=False；daily 读 list_signals。空字符串 horizon/layer 当不过滤。"""
    from invest.db import connect
    from invest.signals.format import format_signals
    from invest.signals.query import list_signals
    from invest.signals.scan import scan
    from invest.signals.types import SESSIONS

    horizon_f = _blank(horizon)
    layer_f = _blank(layer)
    if session not in SESSIONS:
        session = "intraday"
    conn = connect(db_path)
    try:
        if session == "daily":
            rows = list_signals(
                conn, horizon=horizon_f, session="daily", layer=layer_f,
            )
            return format_signals(_rows_to_signals(rows))
        layers = [layer_f] if layer_f else None
        return format_signals(
            scan(conn, session, persist=False, horizon=horizon_f, layers=layers)
        )
    except Exception:
        return ""
    finally:
        conn.close()
