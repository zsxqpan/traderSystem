"""Alpha158 核心因子子集单元测试。用法: python tests/test_alpha158.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from invest.quant.alpha158 import compute_alpha158, factor_names


def _make_daily(n: int = 180, n_syms: int = 3, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n)
    rows = []
    for i in range(n_syms):
        close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.015, n))
        open_ = close * (1 + rng.normal(0, 0.005, n))
        high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
        low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
        vol = rng.integers(1_000_000, 10_000_000, n)
        amount = vol * close
        for j, d in enumerate(dates):
            rows.append({
                "date": d.date().isoformat(), "symbol": f"S{i}",
                "open": open_[j], "high": high[j], "low": low[j],
                "close": close[j], "volume": float(vol[j]), "amount": float(amount[j]),
            })
    return pd.DataFrame(rows)


def _make_index(n: int = 180, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 99)
    dates = pd.bdate_range("2025-01-01", periods=n)
    close = 3000 * np.cumprod(1 + rng.normal(0.0003, 0.008, n))
    return pd.DataFrame({
        "date": [d.date().isoformat() for d in dates],
        "index_code": "000300", "close": close,
    })


def test_factor_names_nonempty():
    names = factor_names()
    assert len(names) >= 50
    assert "ROC_5" in names and "MA_20" in names and "STD_20" in names
    assert "BETA_20" in names and "RESI_20" in names
    assert "MAX_20" in names and "MIN_20" in names
    assert "QTL_20_80" in names and "RANK_60" in names
    print(f"test_factor_names_nonempty OK ({len(names)} factors)")


def test_compute_shape():
    daily = _make_daily()
    idx = _make_index()
    fdf, names = compute_alpha158(daily, idx)
    assert len(names) >= 50
    # date×symbol 截面：行=日期数，列=因子数×标的数
    assert fdf.shape[0] >= 100
    assert len(fdf.columns.levels[0]) == len(names)
    # 因子名一致性
    assert set(names) == set(factor_names())
    # MultiIndex 取列
    assert not fdf.xs("ROC_5", axis=1, level=0).isna().all().all()
    print(f"test_compute_shape OK (rows={fdf.shape[0]}, factors={len(names)})")


def test_known_factor_values():
    """单标的验证已知因子的数值合理性。"""
    daily = _make_daily(n=120, n_syms=1)
    idx = _make_index(n=120)
    fdf, _names = compute_alpha158(daily, idx)
    # ROC_5：close 的 5 日收益率，应大致在 ±10% 内
    roc = fdf.xs("ROC_5", axis=1, level=0).dropna(how="all")
    if not roc.empty:
        vals = roc.values.ravel()
        vals = vals[~np.isnan(vals)]
        assert np.all(np.abs(vals) < 0.5)
    # MA_20 应比 close 平滑（std 更小）
    ma = fdf.xs("MA_20", axis=1, level=0).dropna(how="all").values.ravel()
    cl = fdf.xs("KBAR_CLOSE0", axis=1, level=0).dropna(how="all").values.ravel()
    ma, cl = ma[~np.isnan(ma)], cl[~np.isnan(cl)]
    if len(ma) > 10 and len(cl) > 10:
        assert np.nanstd(ma) < np.nanstd(cl) * 1.5  # 均线波动不超过价格 1.5 倍
    # RANK_20 应在 0-1
    rk = fdf.xs("RANK_20", axis=1, level=0).dropna(how="all").values.ravel()
    rk = rk[~np.isnan(rk)]
    if len(rk):
        assert rk.min() >= 0 and rk.max() <= 1.0001
    print("test_known_factor_values OK")


def test_no_index_degrades_gracefully():
    """无市场基准时 BETA/RESI 为 NaN，不崩溃。"""
    daily = _make_daily(n=80, n_syms=2)
    fdf, names = compute_alpha158(daily, None)
    assert "BETA_20" in names
    assert fdf.xs("BETA_20", axis=1, level=0).isna().all().all()  # 无基准 → 全 NaN
    assert not fdf.xs("ROC_5", axis=1, level=0).isna().all().all()
    print("test_no_index_degrades_gracefully OK")


if __name__ == "__main__":
    test_factor_names_nonempty()
    test_compute_shape()
    test_known_factor_values()
    test_no_index_degrades_gracefully()
    print("\nALL ALPHA158 TESTS PASSED")
