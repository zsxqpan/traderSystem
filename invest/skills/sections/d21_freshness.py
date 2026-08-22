"""D21 数据截至 skill（板块/指数最新日期与滞后天数）。"""
from __future__ import annotations

SKILL = {
    "id": "d21_freshness",
    "name": "数据截至",
    "kind": "section",
    "description": "数据新鲜度：板块/指数最新日期与滞后天数",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.report import _freshness

    conn = connect(db_path)
    try:
        return _freshness(conn)
    finally:
        conn.close()
