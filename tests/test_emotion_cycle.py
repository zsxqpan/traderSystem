"""情绪周期状态机单元测试。用法: python tests/test_emotion_cycle.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.quant.emotion_cycle import cycle_guide, cycle_series, emotion_cycle


def _row(lu, ml, zr, date="2026-08-14"):
    return {"date": date, "limit_up_count": lu, "max_lianban": ml, "zhaban_rate": zr}


def test_freeze():
    r = emotion_cycle(pd.DataFrame([_row(lu=25, ml=2, zr=0.55)]))
    assert r["stage"] == "冰点", r
    assert "涨停" in r["reasons"][0] or "涨停少" in r["reasons"][0]
    print("test_freeze OK")


def test_boom():
    r = emotion_cycle(pd.DataFrame([_row(lu=120, ml=6, zr=0.15)]))
    assert r["stage"] == "主升", r
    print("test_boom OK")


def test_start():
    r = emotion_cycle(pd.DataFrame([_row(lu=60, ml=4, zr=0.30)]))
    assert r["stage"] == "启动", r
    print("test_start OK")


def test_retreat_high_zhaban():
    r = emotion_cycle(pd.DataFrame([_row(lu=90, ml=5, zr=0.60)]))
    assert r["stage"] == "退潮", r  # 炸板率 60% > 50% → 退潮优先于主升
    print("test_retreat_high_zhaban OK")


def test_retreat_drop_from_ma3():
    """涨停数从 3 日均值大幅回落 → 退潮。"""
    df = pd.DataFrame([
        _row(lu=100, ml=6, zr=0.15, date="2026-08-11"),
        _row(lu=110, ml=6, zr=0.18, date="2026-08-12"),
        _row(lu=105, ml=5, zr=0.20, date="2026-08-13"),
        _row(lu=50, ml=4, zr=0.22, date="2026-08-14"),  # 较 3 日均值 105 回落 52%
    ])
    r = emotion_cycle(df)
    assert r["stage"] == "退潮", r
    assert any("回落" in x for x in r["reasons"])
    print("test_retreat_drop_from_ma3 OK")


def test_no_data():
    r = emotion_cycle(pd.DataFrame())
    assert r["stage"] == "数据不足"
    print("test_no_data OK")


def test_cycle_series_and_guide():
    df = pd.DataFrame([
        _row(lu=25, ml=2, zr=0.55, date="2026-08-11"),
        _row(lu=60, ml=4, zr=0.30, date="2026-08-12"),
        _row(lu=120, ml=6, zr=0.15, date="2026-08-13"),
    ])
    s = cycle_series(df)
    assert len(s) == 3
    assert s.iloc[0]["stage"] == "冰点"
    assert s.iloc[1]["stage"] == "启动"
    assert s.iloc[2]["stage"] == "主升"
    assert "空仓" in cycle_guide("冰点")
    assert "止盈" in cycle_guide("退潮")
    print("test_cycle_series_and_guide OK")


if __name__ == "__main__":
    test_freeze()
    test_boom()
    test_start()
    test_retreat_high_zhaban()
    test_retreat_drop_from_ma3()
    test_no_data()
    test_cycle_series_and_guide()
    print("\nALL EMOTION-CYCLE TESTS PASSED")
