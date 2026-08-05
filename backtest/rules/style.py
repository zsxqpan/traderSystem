"""风格标签规则校准：各风格标签的前向（超额）收益统计。"""
from __future__ import annotations

import json

import pandas as pd

from backtest.engine import evaluate_signal, forward_excess, forward_returns
from invest.quant.capital import style_series


def run_style_backtest(
    closes: pd.DataFrame,
    returns: pd.DataFrame,
    benchmark: pd.Series | None = None,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    """closes/returns: date×industry。返回按 风格×周期 的收益统计。"""
    frames = []
    for name, close in closes.items():
        s = close.dropna()
        if len(s) < 80:
            continue
        style = style_series(returns[name].reindex(s.index), s)
        if benchmark is not None:
            fwd = forward_excess(s, benchmark, horizons)
        else:
            fwd = forward_returns(s, horizons)
        df = pd.DataFrame({"industry": name, "style": style})
        df = pd.concat([df, fwd], axis=1)
        df = df[df["style"].isin(["主题炒作", "产业趋势", "超跌修复", "困境反转", "震荡"])]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    all_df = pd.concat(frames, ignore_index=True)
    return evaluate_signal(all_df["style"], all_df[[f"fwd{h}" for h in horizons]])


def metrics_json(stats: pd.DataFrame) -> str:
    return json.dumps(stats.to_dict(orient="records"), ensure_ascii=False)