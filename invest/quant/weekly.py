"""中线轨（周线）强度与趋势：日线重采样为周线后复用短线轨计算。"""
from __future__ import annotations

import pandas as pd

from .indicators import get_params
from .strength import _fmt_date, calc_momentum, calc_rs, calc_trend_stage


def resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """日线（date×列）→ 周五收盘周线。"""
    return daily.resample("W-FRI").last().dropna(how="all")


def compute_weekly(
    closes: pd.DataFrame,
    benchmark: pd.Series,
    params: dict | None = None,
    obj_type: str = "industry",
) -> pd.DataFrame:
    """返回 period='mid' 的 quant_strength 行（周线口径）。"""
    params = params or get_params("weekly_strength")
    closes_w = resample_weekly(closes)
    bench_w = resample_weekly(benchmark.to_frame()).iloc[:, 0]
    rows = []
    for name, close in closes_w.items():
        s = close.dropna()
        if len(s) < 60:  # 周线需至少 60 周
            continue
        rs = calc_rs(s, bench_w, params["rs_windows"], params["rs_weights"])
        mom = calc_momentum(s, params["momentum_windows"])
        stage = calc_trend_stage(s)
        rows.append({
            "run_date": _fmt_date(s.index[-1]),
            "obj_type": obj_type,
            "obj": name,
            "period": "mid",
            "rs": rs,
            "momentum": mom,
            "trend_stage": stage,
            "calc_version": "v1",
        })
    return pd.DataFrame(rows)