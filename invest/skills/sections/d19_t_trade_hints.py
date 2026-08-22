"""D19 做 T 提示 skill（持仓/候选标的盘中实时价：低吸/高抛做 T 候选）。"""
from __future__ import annotations

SKILL = {
    "id": "d19_t_trade_hints",
    "name": "做 T 提示",
    "kind": "section",
    "description": "做 T 提示（实时价日内位置：低吸/高抛候选）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "live": "dict, required（symbol -> 实时价）",
        "pct_map": "dict, required（symbol -> 涨跌幅）",
    },
}


def render(db_path: str, live: dict, pct_map: dict) -> str:
    from invest.db import connect
    from invest.report import _t_trade_hints

    conn = connect(db_path)
    try:
        return "\n".join(_t_trade_hints(conn, live, pct_map))
    finally:
        conn.close()
