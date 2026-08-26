"""A4 周报 skill（周日 20:00 推送；scheduler 再追加周度纪律摘要）。

薄包装 invest.report.weekly_report，输出逐字节一致。
"""
from __future__ import annotations

SKILL = {
    "id": "a4_weekly",
    "name": "周报",
    "kind": "report",
    "description": "周报：评级/仓位/温度/中线强度前8/低估值候选/宏观/消息面(近3日)/周度观点",
    "uses": ["d1_news_block", "d6_macro", "d7_agent_viewpoints", "d8_temp_guide",
             "d9_rating_guide", "d21_freshness", "d22_ratings", "d30_cycle_position"],
    "params": {
        "db_path": "str, required",
        "agent_text": "str, optional, default ''",
    },
}


def render(db_path: str, agent_text: str = "") -> str:
    from invest.report import weekly_report

    return weekly_report(db_path, agent_text)
