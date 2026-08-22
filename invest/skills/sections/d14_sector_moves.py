"""D14 板块异动 skill（最新交易日行业涨幅 TOP n，收盘口径方向参考）。"""
from __future__ import annotations

SKILL = {
    "id": "d14_sector_moves",
    "name": "板块异动",
    "kind": "section",
    "description": "最新交易日行业涨幅 TOP n（收盘口径方向参考）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 3",
    },
}


def render(db_path: str, n: int = 3) -> str:
    from invest.db import connect
    from invest.report import _sector_moves_block

    conn = connect(db_path)
    try:
        return _sector_moves_block(conn, n=n)
    finally:
        conn.close()
