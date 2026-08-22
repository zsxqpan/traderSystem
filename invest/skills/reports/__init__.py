"""报告 skill（6 个）：A0 盘前报告（A1+A2 合并）/ A3 盘后日报 / A4 周报 /
A5 月度复盘推送 / A6 年度复盘推送 / B1 盘中实时报告。

import 本包即完成注册（invest.skills 顶层已触发）。
2026-08-22：a1_premarket、a2_morning_brief 已合并为 a0_premarket。
"""
from __future__ import annotations

from invest.skills.registry import register

from . import (
    a0_premarket,
    a3_daily,
    a4_weekly,
    a5_monthly,
    a6_yearly,
    b1_intraday,
)

for _mod in (a0_premarket, a3_daily, a4_weekly, a5_monthly, a6_yearly, b1_intraday):
    register(_mod.SKILL["id"], _mod)

__all__ = ["a0_premarket", "a3_daily", "a4_weekly", "a5_monthly", "a6_yearly", "b1_intraday"]
