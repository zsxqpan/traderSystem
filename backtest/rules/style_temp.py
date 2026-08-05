"""风格 × 市场温度 交互回测：不同温度区间下各风格标签的前向超额收益。"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from backtest.engine import forward_excess
from invest.quant.capital import style_series
from invest.quant.temperature import temperature_series

REGIME_BINS = [-np.inf, 40, 60, 80, np.inf]
REGIME_LABELS = ["冷<40", "中性40-60", "暖60-80", "热>=80"]

STYLES = ["主题炒作", "产业趋势", "超跌修复", "困境反转", "震荡"]


def run_style_temp_backtest(
    closes: pd.DataFrame,
    returns: pd.DataFrame,
    amounts: pd.DataFrame,
    benchmark: pd.Series,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    """按 风格×温度区间 统计前向超额收益。"""
    temps = temperature_series(returns, amounts)
    regime = pd.cut(temps, bins=REGIME_BINS, labels=REGIME_LABELS)

    frames = []
    for name, close in closes.items():
        s = close.dropna()
        if len(s) < 80:
            continue
        style = style_series(returns[name].reindex(s.index), s)
        fwd = forward_excess(s, benchmark, horizons)
        df = pd.DataFrame({"industry": name, "style": style, "regime": regime.reindex(s.index)})
        df = pd.concat([df, fwd], axis=1)
        df = df[df["style"].isin(STYLES) & df["regime"].notna()]
        frames.append(df)
    if not frames:
        return pd.DataFrame()

    all_df = pd.concat(frames, ignore_index=True)
    rows = []
    for (style, reg), grp in all_df.groupby(["style", "regime"]):
        for h in horizons:
            vals = grp[f"fwd{h}"].dropna()
            if len(vals) < 10:
                continue
            rows.append({
                "style": style,
                "regime": reg,
                "horizon": f"fwd{h}",
                "n": int(len(vals)),
                "mean": round(float(vals.mean()), 5),
                "win_rate": round(float((vals > 0).mean()), 4),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["horizon", "style", "regime"]).reset_index(drop=True)
    return df


def metrics_json(stats: pd.DataFrame) -> str:
    return json.dumps(stats.to_dict(orient="records"), ensure_ascii=False)