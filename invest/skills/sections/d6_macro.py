"""D6 宏观流动性 skill（宏观流动性加工指标一行）。"""
from __future__ import annotations

SKILL = {
    "id": "d6_macro",
    "name": "宏观流动性",
    "kind": "section",
    "description": "宏观流动性加工指标一行",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.report import _macro_text

    conn = connect(db_path)
    try:
        return _macro_text(conn)
    finally:
        conn.close()
