"""轻量回测引擎：前向收益 + 信号统计评估。"""
from __future__ import annotations

import pandas as pd


def forward_returns(close: pd.Series, horizons: tuple[int, ...] = (5, 10, 20)) -> pd.DataFrame:
    """计算每个交易日的前向收益（fwd5/fwd10/fwd20）。"""
    out = {}
    for h in horizons:
        out[f"fwd{h}"] = close.shift(-h) / close - 1
    return pd.DataFrame(out, index=close.index)


def forward_excess(
    close: pd.Series,
    benchmark: pd.Series,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    """前向超额收益：标的 fwd 收益 - 基准 fwd 收益（按日期自动对齐）。"""
    f = forward_returns(close, horizons)
    b = forward_returns(benchmark, horizons)
    return (f - b).dropna(how="all")


def evaluate_signal(signal: pd.Series, fwd: pd.DataFrame, min_n: int = 5) -> pd.DataFrame:
    """按信号标签分组统计前向收益：n / mean / win_rate / std。"""
    rows = []
    for label, idx in signal.groupby(signal).groups.items():
        for col in fwd.columns:
            vals = fwd.loc[idx, col].dropna()
            if len(vals) < min_n:
                continue
            rows.append({
                "signal": str(label),
                "horizon": col,
                "n": int(len(vals)),
                "mean": round(float(vals.mean()), 5),
                "win_rate": round(float((vals > 0).mean()), 4),
                "std": round(float(vals.std()), 5),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["horizon", "signal"]).reset_index(drop=True)
    return df