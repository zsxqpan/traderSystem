"""D10 今日操作建议 skill（温度 + 情绪周期 → 一句话操作建议）。"""
from __future__ import annotations

SKILL = {
    "id": "d10_action_guide",
    "name": "今日操作建议",
    "kind": "section",
    "description": "温度 + 情绪周期 → 一句话操作建议",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "score": "float, optional, default None",
    },
}


def render(db_path: str, score: float | None = None) -> str:
    from invest.db import connect
    from invest.report import _action_guide

    conn = connect(db_path)
    try:
        return _action_guide(conn, score)
    finally:
        conn.close()
