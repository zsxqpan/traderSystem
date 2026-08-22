"""D5 当日涨跌榜 skill（当日板块涨幅/跌幅榜，各前 n，涨幅榜带趋势标签）。"""
from __future__ import annotations

SKILL = {
    "id": "d5_movers",
    "name": "当日涨跌榜",
    "kind": "section",
    "description": "当日板块涨幅/跌幅榜（各前 n，涨幅榜带趋势标签）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 5",
    },
}


def render(db_path: str, n: int = 5) -> str:
    from invest.db import connect
    from invest.report import _movers_block

    conn = connect(db_path)
    try:
        return _movers_block(conn, n=n)
    finally:
        conn.close()
