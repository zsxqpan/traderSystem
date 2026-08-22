"""A1 盘前清单 skill（交易日 08:30 推送）。

薄包装 invest.report.premarket_report，输出逐字节一致。
"""
from __future__ import annotations

SKILL = {
    "id": "a1_premarket",
    "name": "盘前清单",
    "kind": "report",
    "description": "盘前清单：数据截至/仓位/评级/温度/风格/环境重评/关注方向(Agent)",
    "uses": ["d3_style", "d8_temp_guide", "d9_rating_guide", "d21_freshness", "d22_ratings"],
    "params": {
        "db_path": "str, required",
        "agent_text": "str, optional, default ''",
    },
}


def render(db_path: str, agent_text: str = "") -> str:
    from invest.report import premarket_report

    return premarket_report(db_path, agent_text)
