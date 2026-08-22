"""D9 评级→仓位上限 skill（评级 → 建议总仓位上限与倾向）。"""
from __future__ import annotations

SKILL = {
    "id": "d9_rating_guide",
    "name": "评级→仓位上限",
    "kind": "section",
    "description": "评级 → 建议总仓位上限与倾向",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.report import _rating_guide

    conn = connect(db_path)
    try:
        return _rating_guide(conn)
    finally:
        conn.close()
