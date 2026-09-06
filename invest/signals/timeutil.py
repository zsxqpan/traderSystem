"""交易时段时间修正。"""
from __future__ import annotations

import datetime as dt

from invest.signals.thresholds import SKIP_BEFORE


def trading_elapsed_fraction(now: dt.datetime) -> float:
    """已过交易分钟 / 240。集合竞价结束前为 0，收盘后为 1。"""
    t = now.hour * 60 + now.minute
    start_am, end_am = 9 * 60 + 30, 11 * 60 + 30
    start_pm, end_pm = 13 * 60, 15 * 60
    total = 240.0
    if t <= start_am:
        return 0.0
    if t <= end_am:
        return (t - start_am) / total
    if t < start_pm:
        return 120.0 / total
    if t <= end_pm:
        return (120.0 + (t - start_pm)) / total
    return 1.0


def skip_early_volume(now: dt.datetime) -> bool:
    """09:35 前不报缩量/放量（开盘必缩）。"""
    return (now.hour, now.minute) < SKIP_BEFORE
