"""D1 消息面提炼 skill（财联社电报 → LLM 挑重要消息+一句话理由，失败回退直列素材）。"""
from __future__ import annotations

SKILL = {
    "id": "d1_news_block",
    "name": "消息面提炼",
    "kind": "section",
    "description": "消息面：LLM 从财联社电报素材提炼重要消息（失败回退直列素材）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 5",
        "days": "int, optional, default 3",
        "job": "str, optional, default 'weekly'",
    },
}


def render(db_path: str, n: int = 5, days: int = 3, job: str = "weekly") -> str:
    from invest.report import _news_block

    return _news_block(db_path, n=n, days=days, job=job)
