"""市场温度（短线轨）：行业宽度 + 成交集中度 + 平均动量合成 0-100 分。

v1 用行业层面数据近似（涨停家数/连板高度/炸板率待接入后扩展）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import get_params


def temperature_series(
    returns: pd.DataFrame,
    amounts: pd.DataFrame,
    emotion: pd.DataFrame | None = None,
    params: dict | None = None,
) -> pd.Series:
    """逐日温度序列（含 warmup 期）。

    有 market_emotion 数据时叠加真实情绪分量（涨停家数分位/连板/炸板率）；
    否则回退到行业宽度近似。
    """
    params = params or get_params("temperature")
    window = params.get("momentum_window", 5)
    top_n = params.get("top_n", 5)
    if returns.empty:
        return pd.Series(dtype=float)
    ret_w = (1 + returns).rolling(window).apply(np.prod, raw=True) - 1
    breadth = (ret_w > 0).sum(axis=1) / ret_w.notna().sum(axis=1)
    shares = amounts.fillna(0.0).div(amounts.fillna(0.0).sum(axis=1), axis=0)
    top_share = shares.apply(lambda r: float(r.nlargest(top_n).sum()), axis=1)
    avg_mom = ret_w.mean(axis=1)

    emotion = _align_emotion(emotion, returns.index)
    if emotion is not None and len(emotion) >= 10:
        limitups_pct = emotion["limit_up_count"].rank(pct=True).clip(0, 1)
        lianban = emotion["max_lianban"].fillna(0).clip(0, 7) / 7
        zhaban_ok = emotion["zhaban_rate"].fillna(0.0)
        score = (
            breadth * 40
            + limitups_pct * 20
            + lianban * 15
            + (1 - zhaban_ok) * 15
            + top_share * 10
        )
    else:
        score = breadth * 60 + top_share * 20 + (0.5 + avg_mom * 5) * 20
    return score.clip(0, 100)


def _align_emotion(emotion: pd.DataFrame | None, index) -> pd.DataFrame | None:
    if emotion is None or emotion.empty:
        return None
    e = emotion.copy()
    e["date"] = pd.to_datetime(e["date"])
    e = e.set_index("date").reindex(index).sort_index()
    e["limit_up_count"] = pd.to_numeric(e["limit_up_count"], errors="coerce")
    e["max_lianban"] = pd.to_numeric(e["max_lianban"], errors="coerce")
    e["zhaban_rate"] = pd.to_numeric(e["zhaban_rate"], errors="coerce")
    return e.dropna(subset=["limit_up_count"])


def compute_temperature(
    returns: pd.DataFrame,
    amounts: pd.DataFrame,
    emotion: pd.DataFrame | None = None,
    params: dict | None = None,
) -> pd.DataFrame:
    """返回最新一期 quant_temperature 快照行。"""
    params = params or get_params("temperature")
    cols = ["run_date", "limit_up_count", "max_lianban", "zhaban_rate", "profit_effect", "score"]
    if returns.empty:
        return pd.DataFrame(columns=cols)
    series = temperature_series(returns, amounts, emotion, params)
    if series.empty:
        return pd.DataFrame(columns=cols)
    last = series.iloc[-1]
    latest = returns.iloc[-1]
    breadth = float((latest > 0).mean())
    e = _align_emotion(emotion, series.index)
    row = {
        "run_date": _fmt_date(series.index[-1]),
        "limit_up_count": None,
        "max_lianban": None,
        "zhaban_rate": None,
        "profit_effect": round(breadth, 4),
        "score": round(float(last), 2),
    }
    if e is not None and not e.empty:
        last_e = e.iloc[-1]
        row["limit_up_count"] = None if pd.isna(last_e["limit_up_count"]) else int(last_e["limit_up_count"])
        row["max_lianban"] = None if pd.isna(last_e["max_lianban"]) else int(last_e["max_lianban"])
        row["zhaban_rate"] = None if pd.isna(last_e["zhaban_rate"]) else round(float(last_e["zhaban_rate"]), 4)
    return pd.DataFrame([row])


def _fmt_date(d) -> str:
    if hasattr(d, "date"):
        return str(d.date())
    return str(d)