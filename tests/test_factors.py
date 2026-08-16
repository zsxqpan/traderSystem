"""因子自动化（共线性控制 + 拥挤度状态机）单元测试。用法: python tests/test_factors.py"""
from __future__ import annotations

import sys

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))

import numpy as np
import pandas as pd

from invest.quant.collinearity import (
    collinearity_report,
    corr_matrix,
    find_collinear_pairs,
    weight_adjustment,
)
from invest.quant.crowding_state import (
    crowding_state,
    downgrade_ones,
    state_matrix,
)


def _factor_df(n: int = 120) -> pd.DataFrame:
    """构造三个因子：f1/f2 高度相关（>0.6），f3 独立。"""
    rng = np.random.default_rng(42)
    base = rng.normal(0, 1, n)
    f1 = pd.Series(base, index=pd.date_range("2026-01-01", periods=n))
    f2 = pd.Series(base * 0.9 + rng.normal(0, 0.1, n), index=f1.index)  # 与 f1 高度相关
    f3 = pd.Series(rng.normal(0, 1, n), index=f1.index)                 # 独立
    return pd.DataFrame({"f1": f1, "f2": f2, "f3": f3})


def test_corr_matrix():
    df = _factor_df()
    m = corr_matrix(df)
    assert m.shape == (3, 3)
    # f1-f2 高度相关，f1-f3 低相关
    assert abs(m.loc["f1", "f2"]) > 0.9
    assert abs(m.loc["f1", "f3"]) < 0.3
    print("test_corr_matrix OK")


def test_find_collinear_pairs():
    df = _factor_df()
    pairs = find_collinear_pairs(df)
    pair_names = {(p["a"], p["b"]) for p in pairs}
    assert ("f1", "f2") in pair_names
    assert ("f1", "f3") not in pair_names
    assert all(abs(p["corr"]) > 0.60 for p in pairs)
    assert "suggestion" in pairs[0]
    # 高阈值下无违规
    assert find_collinear_pairs(df, threshold=0.99) == []
    print("test_find_collinear_pairs OK")


def test_collinearity_report():
    df = _factor_df()
    r = collinearity_report(df)
    assert r["ok"] is False
    assert r["n_factors"] == 3 and r["n_periods"] == 120
    assert len(r["collinear_pairs"]) >= 1
    r2 = collinearity_report(pd.DataFrame({"only": [1, 2, 3]}))
    assert r2["ok"] is True  # 单因子无违规
    print("test_collinearity_report OK")


def test_weight_adjustment():
    df = _factor_df()
    weights = {"f1": 0.4, "f2": 0.3, "f3": 0.3}
    adj = weight_adjustment(df, weights)
    # 高相关对都降权
    assert adj["f1"] < weights["f1"]
    assert adj["f2"] < weights["f2"]
    print("test_weight_adjustment OK")


def test_crowding_state():
    assert crowding_state(0.5) == "正常"
    assert crowding_state(0.75) == "升温"
    assert crowding_state(0.90) == "高拥挤"
    assert crowding_state(0.96, share_trend=0.02) == "极端但健康"
    assert crowding_state(0.96, share_trend=-0.01) == "极端且恶化"
    assert crowding_state(0.96, share_trend=0.0) == "极端且恶化"  # 平量也视为见顶
    print("test_crowding_state OK")


def test_state_matrix_and_downgrade():
    dates = pd.date_range("2026-06-01", periods=40)
    amounts = pd.DataFrame({
        "A": np.linspace(100, 200, 40),   # 占比上行
        "B": np.linspace(100, 50, 40),    # 占比下行
    }, index=dates)
    crowding = pd.DataFrame([
        {"run_date": "2026-08-14", "obj": "A", "crowding": 0.98},
        {"run_date": "2026-08-14", "obj": "B", "crowding": 0.98},
        {"run_date": "2026-08-14", "obj": "C", "crowding": 0.50},
    ])
    m = state_matrix(crowding, amounts)
    states = dict(zip(m["obj"], m["state"]))
    assert states["A"] == "极端但健康"
    assert states["B"] == "极端且恶化"
    assert states["C"] == "正常"
    downgrade = downgrade_ones(m)
    assert downgrade == ["B"]
    print("test_state_matrix_and_downgrade OK")


if __name__ == "__main__":
    test_corr_matrix()
    test_find_collinear_pairs()
    test_collinearity_report()
    test_weight_adjustment()
    test_crowding_state()
    test_state_matrix_and_downgrade()
    print("\nALL FACTORS TESTS PASSED")
