"""D8 温度→操作倾向 skill（市场温度分 → 一句话操作倾向）。"""
from __future__ import annotations

SKILL = {
    "id": "d8_temp_guide",
    "name": "温度→操作倾向",
    "kind": "section",
    "description": "市场温度分 → 一句话操作倾向",
    "uses": [],
    "params": {
        "score": "float, optional, default None",
    },
}


def render(score: float | None = None) -> str:
    from invest.report import _temp_guide

    return _temp_guide(score)
