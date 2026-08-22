"""D22 评级块 skill（宏观+市场评级，含与上一评级日对比的变化）。"""
from __future__ import annotations

SKILL = {
    "id": "d22_ratings",
    "name": "评级块",
    "kind": "section",
    "description": "评级（宏观+市场）与较上一评级日变化",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.report import _ratings

    conn = connect(db_path)
    try:
        return _ratings(conn)
    finally:
        conn.close()
