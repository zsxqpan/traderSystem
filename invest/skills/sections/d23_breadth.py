"""D23 涨跌家数 skill（最新行业交易日板块上涨/下跌家数）。"""
from __future__ import annotations

SKILL = {
    "id": "d23_breadth",
    "name": "涨跌家数",
    "kind": "section",
    "description": "最新行业交易日板块上涨/下跌家数",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.pipeline import _breadth

    conn = connect(db_path)
    try:
        return _breadth(conn)
    finally:
        conn.close()
