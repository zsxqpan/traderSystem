"""报告 skill（7 个）：A1 盘前清单 / A2 盘前信息早报 / A3 盘后日报 / A4 周报 /
A5 月度复盘推送 / A6 年度复盘推送 / B1 盘中实时报告。

import 本包即完成注册（invest.skills 顶层已触发）。
"""
from __future__ import annotations

from invest.skills.registry import register

from . import (
    a1_premarket,
    a2_morning_brief,
    a3_daily,
    a4_weekly,
    a5_monthly,
    a6_yearly,
    b1_intraday,
)

for _mod in (a1_premarket, a2_morning_brief, a3_daily, a4_weekly,
             a5_monthly, a6_yearly, b1_intraday):
    register(_mod.SKILL["id"], _mod)

__all__ = ["a1_premarket", "a2_morning_brief", "a3_daily", "a4_weekly",
           "a5_monthly", "a6_yearly", "b1_intraday"]
