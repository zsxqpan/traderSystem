"""D25 外围影响·LLM 解读 skill（2026-08-22 新增）。"""
from __future__ import annotations

SKILL = {
    "id": "d25_overnight_analysis",
    "name": "外围影响解读",
    "kind": "section",
    "description": "隔夜外围对今日 A 股的影响解读（LLM，2-4 句，失败省略）",
    "uses": ["d24_global_snapshot"],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.skills.sections._digest import overnight_analysis

    return overnight_analysis(db_path)
