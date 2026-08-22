"""B1 盘中实时报告 skill（@机器人/私聊/语义触发）。

薄包装 invest.report.intraday_report；3800 字截断与 30s 限频留在 feishu_ws（调用方）。
"""
from __future__ import annotations

SKILL = {
    "id": "b1_intraday",
    "name": "盘中实时报告",
    "kind": "report",
    "description": "盘中实时报告（brief 简洁 / detailed 完整 / public 去持仓警戒）",
    "uses": ["d4_strength", "d8_temp_guide", "d9_rating_guide", "d10_action_guide",
             "d11_emotion", "d12_limit_up_ladder", "d13_fund_line", "d14_sector_moves",
             "d15_capital_leaders", "d16_card_alerts", "d18_abnormal_moves",
             "d19_t_trade_hints", "d20_entry_timing", "d21_freshness"],
    "params": {
        "db_path": "str, required",
        "public": "bool, optional, default False",
        "brief": "bool, optional, default True",
    },
}


def render(db_path: str, public: bool = False, brief: bool = True) -> str:
    from invest.report import intraday_report

    return intraday_report(db_path, public=public, brief=brief)
