"""因子有效性检验单元测试。用法: python tests/test_factor_eval.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from backtest.factor_eval import (
    factor_eval_report,
    group_monotonicity,
    icir,
    rolling_ic,
)


def _panel(n_dates: int = 80, n_assets: int = 30, seed: int = 1):
    """构造面板：因子与 fwd 收益有真实关系的合成数据。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-02", periods=n_dates)
    assets = [f"A{i:02d}" for i in range(n_assets)]
    # 因子：资产有固定特征（alpha_i），随时间漂移
    alpha = rng.normal(0, 0.05, n_assets)
    factor = pd.DataFrame(
        {a: alpha[j] + rng.normal(0, 0.02, n_dates) for j, a in enumerate(assets)},
        index=dates,
    )
    # fwd 收益：与因子正相关 + 噪声
    fwd = pd.DataFrame(
        {a: 0.3 * factor[a] + rng.normal(0, 0.05, n_dates) for a in assets},
        index=dates,
    )
    return factor, fwd


def test_rolling_ic_positive():
    factor, fwd = _panel()
    ic = rolling_ic(factor, fwd, window=20)
    assert not ic.empty
    # 构造的因子与收益正相关 -> 滚动 IC 均值应为正
    assert ic.mean() > 0
    print("test_rolling_ic_positive OK")


def test_icir():
    factor, fwd = _panel()
    ic = rolling_ic(factor, fwd, window=20)
    ir = icir(ic, window=20)
    assert not ir.empty
    assert ir.mean() > 0
    print("test_icir OK")


def test_group_monotonicity():
    factor, fwd = _panel()
    g = group_monotonicity(factor, fwd, n_groups=5)
    assert not g.empty
    assert "monotonic_hint" in g.columns
    # 因子最高组收益应高于最低组
    top = g[g["group"] == 5]["mean_ret"].iloc[0]
    bottom = g[g["group"] == 1]["mean_ret"].iloc[0]
    assert top > bottom
    print("test_group_monotonicity OK")


def test_factor_eval_report():
    factor, fwd = _panel()
    r = factor_eval_report(factor, fwd, window=20)
    assert r["ok"] is True
    assert r["ic_mean"] > 0
    assert r["icir"] > 0
    assert len(r["conclusions"]) >= 1
    # 无数据 -> ok False
    empty = factor_eval_report(pd.DataFrame(), pd.DataFrame())
    assert empty["ok"] is False
    print("test_factor_eval_report OK")


def test_noise_factor_report():
    """纯噪声因子：IC 应接近 0，结论为无效。"""
    rng = np.random.default_rng(9)
    dates = pd.bdate_range("2026-01-02", periods=60)
    assets = [f"B{i:02d}" for i in range(20)]
    factor = pd.DataFrame(rng.normal(0, 1, (60, 20)), index=dates, columns=assets)
    fwd = pd.DataFrame(rng.normal(0, 0.03, (60, 20)), index=dates, columns=assets)
    r = factor_eval_report(factor, fwd, window=20)
    assert r["ok"] is True
    assert abs(r["ic_mean"]) < 0.05
    assert any("无效" in c for c in r["conclusions"])
    print("test_noise_factor_report OK")


if __name__ == "__main__":
    test_rolling_ic_positive()
    test_icir()
    test_group_monotonicity()
    test_factor_eval_report()
    test_noise_factor_report()
    print("\nALL FACTOR EVAL TESTS PASSED")
