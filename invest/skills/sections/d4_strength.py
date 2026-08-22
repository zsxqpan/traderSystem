"""D4 强度榜 skill（行业/个股相对强度；短线带 RS5/10/20，中线只有 rs）。"""
from __future__ import annotations

SKILL = {
    "id": "d4_strength",
    "name": "强度榜",
    "kind": "section",
    "description": "行业/个股强度榜（短线带 RS5/10/20，中线只有 rs）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "period": "str, optional, default 'short'",
        "n": "int, optional, default 5",
    },
}


def render(db_path: str, period: str = "short", n: int = 5) -> str:
    from invest.db import connect
    from invest.report import _strength_block

    conn = connect(db_path)
    try:
        return _strength_block(conn, period=period, n=n)
    finally:
        conn.close()
