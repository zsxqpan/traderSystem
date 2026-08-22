"""A2 盘前信息早报 skill（交易日 08:40 推送，仅飞书）。

薄包装 invest.report.morning_brief_report，输出逐字节一致。
"""
from __future__ import annotations

SKILL = {
    "id": "a2_morning_brief",
    "name": "盘前信息早报",
    "kind": "report",
    "description": "盘前信息早报：隔夜市场/外围/龙虎榜/板块主线/今日关注/仓位/宏观",
    "uses": ["d5_movers", "d6_macro", "d8_temp_guide", "d9_rating_guide",
             "d15_capital_leaders", "d18_abnormal_moves", "d21_freshness"],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.report import morning_brief_report

    return morning_brief_report(db_path)
