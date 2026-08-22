"""A3 盘后日报 skill（每日 22:00 推送；scheduler 再追加【今日】统计与数据质量）。

薄包装 invest.report.daily_report，输出逐字节一致。
"""
from __future__ import annotations

SKILL = {
    "id": "a3_daily",
    "name": "盘后日报",
    "kind": "report",
    "description": "盘后日报：宏观→温度/仓位/评级→风格→情绪→板块→重点行业→强度→异动→候选池→消息面→持仓警戒→Agent复盘",
    "uses": ["d1_news_block", "d2_focus_industries", "d3_style", "d4_strength",
             "d6_macro", "d7_agent_viewpoints", "d8_temp_guide", "d9_rating_guide",
             "d16_card_alerts", "d17_pool_delta", "d18_abnormal_moves", "d20_entry_timing",
             "d21_freshness", "d22_ratings"],
    "params": {
        "db_path": "str, required",
        "agent_text": "str, optional, default ''",
    },
}


def render(db_path: str, agent_text: str = "") -> str:
    from invest.report import daily_report

    return daily_report(db_path, agent_text)
