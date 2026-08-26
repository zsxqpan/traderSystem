"""D28 社区热议 skill（2026-08-23：复用 opinion-analysis 方法论，雪球/股吧讨论 → LLM 提炼）。"""
from __future__ import annotations

SKILL = {
    "id": "d28_community_hot",
    "name": "社区热议",
    "kind": "section",
    "description": "社区热议：必应搜索雪球/股吧讨论 → LLM 提炼 2-3 条（失败回退直列素材）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 3",
        "job": "str, optional, default 'daily_report'",
    },
}


def render(db_path: str, n: int = 3, job: str = "daily_report") -> str:
    from invest.skills.sections._community import community_hot

    return community_hot(db_path, n=n, job=job)
