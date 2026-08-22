"""D13 资金主线 skill（行业板块主力净流入 TOP n，东财行业资金流）。"""
from __future__ import annotations

SKILL = {
    "id": "d13_fund_line",
    "name": "资金主线",
    "kind": "section",
    "description": "行业板块主力净流入 TOP n（东财行业资金流）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 3",
    },
}


def render(db_path: str, n: int = 3) -> str:
    from invest.db import connect
    from invest.report import _fund_line_block

    conn = connect(db_path)
    try:
        return _fund_line_block(conn, n=n)
    finally:
        conn.close()
