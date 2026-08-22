"""小节 skill（23 个）：D1-D23，薄包装 invest.report / invest.pipeline 的现有小节函数。

render 输出规则：
- 返回 str 的小节：原样返回（逐字节一致）；
- 返回 list[str] 的小节（d16/d19/d20）："\n".join 为多行文本；
- 返回 list[dict] 的小节（d18）：按报告常用格式逐项格式化。

import 本包即完成注册（invest.skills 顶层已触发）。
"""
from __future__ import annotations

from invest.skills.registry import register

from . import (
    d1_news_block,
    d2_focus_industries,
    d3_style,
    d4_strength,
    d5_movers,
    d6_macro,
    d7_agent_viewpoints,
    d8_temp_guide,
    d9_rating_guide,
    d10_action_guide,
    d11_emotion,
    d12_limit_up_ladder,
    d13_fund_line,
    d14_sector_moves,
    d15_capital_leaders,
    d16_card_alerts,
    d17_pool_delta,
    d18_abnormal_moves,
    d19_t_trade_hints,
    d20_entry_timing,
    d21_freshness,
    d22_ratings,
    d23_breadth,
)

_SECTION_MODULES = (
    d1_news_block, d2_focus_industries, d3_style, d4_strength, d5_movers, d6_macro,
    d7_agent_viewpoints, d8_temp_guide, d9_rating_guide, d10_action_guide, d11_emotion,
    d12_limit_up_ladder, d13_fund_line, d14_sector_moves, d15_capital_leaders,
    d16_card_alerts, d17_pool_delta, d18_abnormal_moves, d19_t_trade_hints,
    d20_entry_timing, d21_freshness, d22_ratings, d23_breadth,
)

for _mod in _SECTION_MODULES:
    register(_mod.SKILL["id"], _mod)

__all__ = [m.SKILL["id"] for m in _SECTION_MODULES]
