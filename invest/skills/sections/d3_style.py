"""D3 市场风格 skill（多指数风格/结构性行情判断）。"""
from __future__ import annotations

SKILL = {
    "id": "d3_style",
    "name": "市场风格",
    "kind": "section",
    "description": "多指数风格/结构性行情判断",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.report import _style_block

    conn = connect(db_path)
    try:
        return _style_block(conn)
    finally:
        conn.close()
