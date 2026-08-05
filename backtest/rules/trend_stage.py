"""趋势阶段分类规则校准：各阶段标签的前向收益统计。"""
from __future__ import annotations

import json

import pandas as pd

from backtest.engine import evaluate_signal, forward_excess, forward_returns
from invest.quant.strength import stage_series


def run_trend_stage_backtest(
    closes: pd.DataFrame,
    benchmark: pd.Series | None = None,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    """closes: date×industry 日线收盘。benchmark 提供时使用前向超额收益。"""
    frames = []
    for name, close in closes.items():
        s = close.dropna()
        if len(s) < 80:
            continue
        stage = stage_series(s)
        if benchmark is not None:
            fwd = forward_excess(s, benchmark, horizons)
        else:
            fwd = forward_returns(s, horizons)
        df = pd.DataFrame({"industry": name, "stage": stage})
        df = pd.concat([df, fwd], axis=1)
        df = df[df["stage"].isin(["启动", "加速", "减速", "背离", "破位", "震荡"])]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    all_df = pd.concat(frames, ignore_index=True)
    return evaluate_signal(all_df["stage"], all_df[[f"fwd{h}" for h in horizons]])


def metrics_json(stats: pd.DataFrame) -> str:
    """把统计结果转 JSON（供 backtest_runs.metrics_json 存档）。"""
    return json.dumps(stats.to_dict(orient="records"), ensure_ascii=False)