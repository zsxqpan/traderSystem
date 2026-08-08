"""行业/标的相对强度、多周期动量与趋势阶段（短线轨）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import get_params


def _window_return(series: pd.Series, window: int) -> float:
    """区间累计收益：series.iloc[-1] / series.iloc[-1-window] - 1。"""
    if len(series) <= window:
        return float("nan")
    return float(series.iloc[-1] / series.iloc[-1 - window] - 1)


def calc_rs(
    industry: pd.Series,
    benchmark: pd.Series,
    windows: list[int],
    weights: list[float],
) -> float:
    """相对强度：多窗口超额收益加权。industry/benchmark 已按日期对齐。"""
    df = pd.concat([industry, benchmark], axis=1, keys=["ind", "bench"]).dropna()
    if len(df) <= max(windows):
        return float("nan")
    rs, total = 0.0, 0.0
    for w, wt in zip(windows, weights):
        rs += wt * (_window_return(df["ind"], w) - _window_return(df["bench"], w))
        total += wt
    return float(rs / total) if total else float("nan")


def calc_momentum(series: pd.Series, windows: list[int]) -> float:
    """多周期动量：各窗口区间收益的均值。"""
    vals = [_window_return(series, w) for w in windows]
    vals = [v for v in vals if not np.isnan(v)]
    return float(np.mean(vals)) if vals else float("nan")


def stage_series(close: pd.Series) -> pd.Series:
    """向量化趋势阶段序列：破位 / 背离 / 加速 / 减速 / 启动 / 震荡 / 数据不足。"""
    s = close
    ma20 = s.rolling(20).mean()
    ma60 = s.rolling(60).mean()
    mom5 = s.pct_change(5)
    mom5_prev = s.shift(5).pct_change(5)
    cur = s
    out = pd.Series("震荡", index=s.index, dtype=object)
    out[(cur < ma60) & (ma20 < ma60)] = "破位"
    out[(cur > ma20) & (mom5 < -0.02)] = "背离"
    out[(ma20 > ma60) & (cur > ma20) & (mom5 > mom5_prev)] = "加速"
    out[(ma20 > ma60) & (cur > ma20) & (mom5 <= mom5_prev)] = "减速"
    out[(ma20 <= ma60) & (cur > ma20)] = "启动"
    out[ma20.isna() | ma60.isna()] = "数据不足"
    return out


def calc_trend_stage(close: pd.Series) -> str:
    """最新一期趋势阶段（复用 stage_series）。"""
    s = close.dropna()
    if len(s) < 60:
        return "数据不足"
    return str(stage_series(s).iloc[-1])


def compute_strength(
    closes: pd.DataFrame,
    benchmark: pd.Series,
    params: dict | None = None,
    obj_type: str = "industry",
) -> pd.DataFrame:
    """closes: date×industry 收盘价；benchmark: 基准收盘价。

    返回最新一期快照：run_date / obj_type / obj / period / rs / momentum / trend_stage / calc_version。
    """
    params = params or get_params("strength")
    windows = params["rs_windows"]
    weights = params["rs_weights"]
    rows = []
    for name, close in closes.items():
        rs = calc_rs(close, benchmark, windows, weights)
        mom = calc_momentum(close, params["momentum_windows"])
        stage = calc_trend_stage(close)
        last_date = close.dropna().index[-1]
        # 单窗口相对强度（日报展示 5/10/20 日超额）
        win_rs = {f"rs{w}": calc_rs(close, benchmark, [w], [1.0]) for w in windows}
        rows.append({
            "run_date": _fmt_date(last_date),
            "obj_type": obj_type,
            "obj": name,
            "period": "short",
            "rs": rs,
            "rs5": win_rs["rs5"],
            "rs10": win_rs["rs10"],
            "rs20": win_rs["rs20"],
            "momentum": mom,
            "trend_stage": stage,
            "calc_version": "v1",
        })
    return pd.DataFrame(rows)


def _fmt_date(d) -> str:
    if hasattr(d, "date"):
        return str(d.date())
    return str(d)