"""D17 候选池变化 skill（当日候选池：新入池/移出/等级变化 → 操作提示）。"""
from __future__ import annotations

SKILL = {
    "id": "d17_pool_delta",
    "name": "候选池变化",
    "kind": "section",
    "description": "当日候选池变化：新入池/移出/等级变化",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.report import _pool_delta

    conn = connect(db_path)
    try:
        return _pool_delta(conn)
    finally:
        conn.close()
