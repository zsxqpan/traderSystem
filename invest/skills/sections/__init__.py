"""小节 skill（31 个）：D1-D23 + D24-D27（2026-08-22 盘前报告新增）+ D28-D31（2026-08-23 角度 skill 复用）。

render 输出规则：
- 返回 str 的小节：原样返回（逐字节一致）；
- 返回 list[str] 的小节（d16/d19/d20）："\n".join 为多行文本；
- 返回 list[dict] 的小节（d18）：按报告常用格式逐项格式化；
- d24-d27（盘前）：文本视图；表格结构由 a0_premarket 组装（直接调底层函数）。

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
    d24_global_snapshot,
    d25_overnight_analysis,
    d26_market_watch,
    d27_news_digest,
    d28_community_hot,
    d29_sector_resonance,
    d30_cycle_position,
    d31_pool_trap_alerts,
)

_SECTION_MODULES = (
    d1_news_block, d2_focus_industries, d3_style, d4_strength, d5_movers, d6_macro,
    d7_agent_viewpoints, d8_temp_guide, d9_rating_guide, d10_action_guide, d11_emotion,
    d12_limit_up_ladder, d13_fund_line, d14_sector_moves, d15_capital_leaders,
    d16_card_alerts, d17_pool_delta, d18_abnormal_moves, d19_t_trade_hints,
    d20_entry_timing, d21_freshness, d22_ratings, d23_breadth,
    d24_global_snapshot, d25_overnight_analysis, d26_market_watch, d27_news_digest,
    d28_community_hot, d29_sector_resonance, d30_cycle_position, d31_pool_trap_alerts,
)

for _mod in _SECTION_MODULES:
    register(_mod.SKILL["id"], _mod)

__all__ = [m.SKILL["id"] for m in _SECTION_MODULES]
