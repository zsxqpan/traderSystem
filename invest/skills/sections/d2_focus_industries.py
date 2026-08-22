"""D2 重点关注行业 skill（名单内行业四维数据 + LLM 一句话意见，失败只出数据）。"""
from __future__ import annotations

SKILL = {
    "id": "d2_focus_industries",
    "name": "重点关注行业",
    "kind": "section",
    "description": "重点关注行业：四维数据 + LLM 一句话意见（失败只出数据）",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.report import _focus_industries_block

    conn = connect(db_path)
    try:
        return _focus_industries_block(conn, db_path)
    finally:
        conn.close()
