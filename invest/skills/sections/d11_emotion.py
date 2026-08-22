"""D11 情绪·人气 skill（最新交易日涨停/最高连板/炸板率 + 情绪周期阶段）。"""
from __future__ import annotations

SKILL = {
    "id": "d11_emotion",
    "name": "情绪·人气",
    "kind": "section",
    "description": "最新交易日涨停/最高连板/炸板率 + 情绪周期阶段",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.report import _emotion_block

    conn = connect(db_path)
    try:
        return _emotion_block(conn)
    finally:
        conn.close()
