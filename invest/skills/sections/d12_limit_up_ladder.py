"""D12 连板梯队·涨停龙头 skill（东财涨停池个股明细，盘中实时）。"""
from __future__ import annotations

SKILL = {
    "id": "d12_limit_up_ladder",
    "name": "连板梯队·涨停龙头",
    "kind": "section",
    "description": "连板梯队/涨停龙头（东财涨停池，盘中实时）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 6",
    },
}


def render(db_path: str, n: int = 6) -> str:
    from invest.db import connect
    from invest.report import _limit_up_ladder_block

    conn = connect(db_path)
    try:
        return _limit_up_ladder_block(conn, n=n)
    finally:
        conn.close()
