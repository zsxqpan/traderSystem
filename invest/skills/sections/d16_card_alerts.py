"""D16 持仓警戒 skill（持仓卡片警戒：破止损/近止损/近目标，可传实时价）。"""
from __future__ import annotations

SKILL = {
    "id": "d16_card_alerts",
    "name": "持仓警戒",
    "kind": "section",
    "description": "持仓卡片警戒：破止损/近止损/近目标（可传实时价）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "live_prices": "dict, optional, default None",
    },
}


def render(db_path: str, live_prices: dict | None = None) -> str:
    from invest.db import connect
    from invest.report import _card_alerts

    conn = connect(db_path)
    try:
        return "\n".join(_card_alerts(conn, live_prices=live_prices))
    finally:
        conn.close()
