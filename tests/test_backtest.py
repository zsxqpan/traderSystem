"""回测框架单元测试。用法: python tests/test_backtest.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from backtest.engine import evaluate_signal, forward_returns
from backtest.rules.trend_stage import run_trend_stage_backtest
from invest.quant.strength import stage_series


def test_forward_returns():
    s = pd.Series([100.0, 110.0, 121.0], index=pd.date_range("2024-01-01", periods=3))
    f = forward_returns(s, horizons=(1,))
    assert abs(f["fwd1"].iloc[0] - 0.10) < 1e-9
    assert pd.isna(f["fwd1"].iloc[-1])
    print("test_forward_returns OK")


def test_evaluate_signal():
    sig = pd.Series(["up", "up", "up", "down", "down", "down"])
    fwd = pd.DataFrame({"fwd1": [0.01, 0.02, 0.03, -0.01, -0.02, -0.03]})
    df = evaluate_signal(sig, fwd, min_n=1)
    up = df[df["signal"] == "up"].iloc[0]
    down = df[df["signal"] == "down"].iloc[0]
    assert up["mean"] > 0 and down["mean"] < 0
    assert up["win_rate"] == 1.0 and down["win_rate"] == 0.0
    print("test_evaluate_signal OK")


def test_stage_series():
    dates = pd.bdate_range("2024-01-02", periods=120)
    down = pd.Series(100 * np.full(120, 0.996).cumprod(), index=dates)
    stages = stage_series(down)
    assert stages.iloc[-1] == "破位"
    up = pd.Series(100 * np.full(120, 1.005).cumprod(), index=dates)
    assert stage_series(up).iloc[-1] in ("加速", "减速")
    print("test_stage_series OK")


def test_trend_stage_backtest_runs():
    dates = pd.bdate_range("2024-01-02", periods=150)
    rng = np.random.default_rng(1)
    up = pd.Series(100 * np.cumprod(1 + rng.normal(0.002, 0.008, 150)), index=dates)
    down = pd.Series(100 * np.cumprod(1 + rng.normal(-0.002, 0.008, 150)), index=dates)
    closes = pd.DataFrame({"UP": up, "DOWN": down}, index=dates)
    stats = run_trend_stage_backtest(closes, horizons=(5,))
    assert not stats.empty
    assert {"signal", "horizon", "n", "mean", "win_rate", "std"} <= set(stats.columns)
    print("test_trend_stage_backtest_runs OK")




def test_forward_excess():
    from backtest.engine import forward_excess
    dates = pd.date_range("2024-01-01", periods=4)
    close = pd.Series([100.0, 108.0, 110.0, 112.0], index=dates)      # 首日 +8%
    bench = pd.Series([100.0, 105.0, 106.0, 107.0], index=dates)      # 首日 +5%
    ex = forward_excess(close, bench, horizons=(1,))
    assert abs(ex["fwd1"].iloc[0] - 0.03) < 1e-9
    print("test_forward_excess OK")


def test_style_series():
    from invest.quant.capital import style_series
    dates = pd.bdate_range("2024-01-02", periods=120)
    steady = pd.Series(100 * np.full(120, 1.005).cumprod(), index=dates)
    rng = np.random.default_rng(3)
    rets = rng.normal(0.0, 0.025, 120)
    rets[115:120] = 0.06
    choppy = pd.Series(100 * np.cumprod(1 + rets), index=dates)
    assert style_series(steady.pct_change(), steady).iloc[-1] == "产业趋势"
    assert style_series(choppy.pct_change(), choppy).iloc[-1] == "主题炒作"
    print("test_style_series OK")



def test_temperature_series():
    from invest.quant.temperature import temperature_series
    n = 120
    dates = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(9)
    hot_ret = pd.DataFrame({
        "A": rng.normal(0.004, 0.012, n),
        "B": rng.normal(0.004, 0.012, n),
        "C": rng.normal(0.004, 0.012, n),
    }, index=dates)
    cold_ret = pd.DataFrame({
        "A": rng.normal(-0.004, 0.012, n),
        "B": rng.normal(-0.004, 0.012, n),
        "C": rng.normal(-0.004, 0.012, n),
    }, index=dates)
    amt = pd.DataFrame({"A": 100.0, "B": 100.0, "C": 100.0}, index=dates)
    s_hot = temperature_series(hot_ret, amt)
    s_cold = temperature_series(cold_ret, amt)
    assert s_hot.mean() > s_cold.mean(), (s_hot.mean(), s_cold.mean())
    print("test_temperature_series OK")


def test_style_temp_backtest_runs():
    from backtest.rules.style_temp import run_style_temp_backtest
    n = 150
    dates = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(4)
    rets = pd.DataFrame({
        "H": rng.normal(0.004, 0.02, n),   # 高波动上行
        "S": rng.normal(0.001, 0.008, n),  # 平稳
        "D": rng.normal(-0.003, 0.012, n), # 下行
    }, index=dates)
    closes = 100 * (1 + rets).cumprod()
    bench = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.006, n)), index=dates)
    amt = pd.DataFrame({"H": 100.0, "S": 100.0, "D": 100.0}, index=dates)
    df = run_style_temp_backtest(closes, rets, amt, bench, horizons=(5,))
    assert not df.empty
    assert {"style", "regime", "horizon", "n", "mean", "win_rate"} <= set(df.columns)
    print("test_style_temp_backtest_runs OK")



def test_rating_map():
    from backtest.rules.rating_map import run_rating_map_backtest
    dates = pd.bdate_range("2021-01-01", periods=600)
    # 构造：前300天上涨（进攻），后300天下跌（防守）
    idx_close = pd.Series(
        np.concatenate([
            np.linspace(100, 200, 300),
            np.linspace(200, 120, 300),
        ]), index=dates,
    )
    months = pd.date_range("2020-12", periods=60, freq="MS")
    macro = pd.concat([
        pd.DataFrame({"date": [d.strftime("%Y年%m月份") for d in months], "indicator": "货币(M1)-同比增长", "value": [8.0] * 60}),
        pd.DataFrame({"date": [d.strftime("%Y年%m月份") for d in months], "indicator": "货币和准货币(M2)-同比增长", "value": [9.0] * 60}),
    ], ignore_index=True)  # 剪刀差 -1 → 中性
    result = run_rating_map_backtest(idx_close, macro)
    assert not result["stats"].empty
    assert result["n_cells"] > 0
    for v in result["suggestion"].values():
        assert 0.05 <= v <= 0.80
    # 收紧（剪刀差 -3）时均值应低于中性（剪刀差 -1）
    macro_tight = pd.concat([
        pd.DataFrame({"date": [d.strftime("%Y年%m月份") for d in months], "indicator": "货币(M1)-同比增长", "value": [6.0] * 60}),
        pd.DataFrame({"date": [d.strftime("%Y年%m月份") for d in months], "indicator": "货币和准货币(M2)-同比增长", "value": [9.0] * 60}),
    ], ignore_index=True)
    r2 = run_rating_map_backtest(idx_close, macro_tight)
    assert "中性/收紧" in r2["suggestion"] or "进攻/收紧" in r2["suggestion"]
    print("test_rating_map OK")

if __name__ == "__main__":
    test_forward_returns()
    test_evaluate_signal()
    test_stage_series()
    test_trend_stage_backtest_runs()
    test_forward_excess()
    test_style_series()
    test_temperature_series()
    test_style_temp_backtest_runs()
    test_rating_map()
    print("\nALL BACKTEST TESTS PASSED")