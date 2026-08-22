"""D15 资金焦点·龙虎榜龙头 skill（最新龙虎榜净买入 TOP n）。"""
from __future__ import annotations

SKILL = {
    "id": "d15_capital_leaders",
    "name": "资金焦点·龙虎榜龙头",
    "kind": "section",
    "description": "最新龙虎榜净买入 TOP n",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 3",
    },
}


def render(db_path: str, n: int = 3) -> str:
    from invest.db import connect
    from invest.report import _capital_leaders_block

    conn = connect(db_path)
    try:
        return _capital_leaders_block(conn, n=n)
    finally:
        conn.close()
