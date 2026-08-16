"""回撤/损失限额与压力测试单元测试。用法: python tests/test_limits.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.discipline.limits import (
    DrawdownLevels,
    daily_loss_check,
    drawdown_stage,
    stress_test,
    weekly_loss_check,
    worst_scenario,
)


def test_drawdown_stage():
    # 正常
    r = drawdown_stage(100.0, 96.0)  # 4% 回撤
    assert r["stage"] == "normal"
    # 预警 5%
    r2 = drawdown_stage(100.0, 94.0)
    assert r2["stage"] == "warn"
    # 强减 8%
    r3 = drawdown_stage(100.0, 90.0)
    assert r3["stage"] == "reduce"
    assert any("半仓" in a for a in r3["actions"])
    # 清仓 12%
    r4 = drawdown_stage(100.0, 86.0)
    assert r4["stage"] == "clear"
    # 停摆 15%
    r5 = drawdown_stage(100.0, 84.0)
    assert r5["stage"] == "halt"
    # 边界：刚好 15% -> halt
    r6 = drawdown_stage(100.0, 85.0)
    assert r6["stage"] == "halt"
    # peak<=0 -> normal
    r7 = drawdown_stage(0.0, 50.0)
    assert r7["stage"] == "normal"
    print("test_drawdown_stage OK")


def test_daily_weekly_loss():
    # 单日亏损 1% -> 不拦
    d1 = daily_loss_check(-1000.0, 100000.0)
    assert d1["blocked"] is False
    # 单日亏损 2% -> 拦
    d2 = daily_loss_check(-2000.0, 100000.0)
    assert d2["blocked"] is True
    assert "单日" in d2["reason"]
    # 单周亏损 4% -> 拦
    w1 = weekly_loss_check(-4000.0, 100000.0)
    assert w1["blocked"] is True
    assert "单周" in w1["reason"]
    # 单周盈利 -> 不拦
    w2 = weekly_loss_check(5000.0, 100000.0)
    assert w2["blocked"] is False
    # 自定义阈值
    d3 = daily_loss_check(-3000.0, 100000.0, DrawdownLevels(daily_loss=0.05))
    assert d3["blocked"] is False
    print("test_daily_weekly_loss OK")


def test_stress_test():
    stress = stress_test(100000.0)
    assert len(stress) == 5
    # 各场景 drawdown = -shock
    for s in stress:
        assert s["drawdown"] == round(-s["shock"], 4)
        assert s["equity_after"] == round(100000 * (1 + s["shock"]), 2)
    # 最坏场景：创业板跌停 -20%
    worst = worst_scenario(stress)
    assert worst["scenario"].startswith("跌停无法退出-创业板")
    assert worst["stage"] == "halt"
    # 全仓低开 5% -> warn
    low_open = [s for s in stress if s["scenario"] == "全仓低开5%"][0]
    assert low_open["stage"] == "warn"
    # 空列表
    assert worst_scenario([]) == {}
    print("test_stress_test OK")


if __name__ == "__main__":
    test_drawdown_stage()
    test_daily_weekly_loss()
    test_stress_test()
    print("\nALL LIMITS TESTS PASSED")
