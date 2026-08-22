"""D7 Agent 复盘/周度观点 skill（最新 active 投研/交易观点，含结论/周期/失效条件）。"""
from __future__ import annotations

SKILL = {
    "id": "d7_agent_viewpoints",
    "name": "Agent 复盘/周度观点",
    "kind": "section",
    "description": "最新 active 投研/交易观点（结论/周期/失效条件）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 5",
    },
}


def render(db_path: str, n: int = 5) -> str:
    from invest.db import connect
    from invest.report import _agent_viewpoints

    conn = connect(db_path)
    try:
        return _agent_viewpoints(conn, n=n)
    finally:
        conn.close()
