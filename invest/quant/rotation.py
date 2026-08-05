"""板块轮动与博弈（短线轨）：行业排名变化、成交额占比。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import get_params


def compute_rotation(
    returns: pd.DataFrame,
    amounts: pd.DataFrame,
    params: dict | None = None,
) -> pd.DataFrame:
    """returns/amounts: date×industry。

    返回最新一期快照：run_date / industry / rank / lead_lag / turnover_share。
    rank=1 为最强；lead_lag: 领涨/滞后/同步（基于排名变化）。
    """
    params = params or get_params("rotation")
    if len(returns) < 2 or returns.empty:
        return pd.DataFrame(columns=["run_date", "industry", "rank", "lead_lag", "turnover_share"])
    latest = returns.iloc[-1]
    prev = returns.iloc[-2]
    rank_latest = latest.rank(ascending=False)
    rank_prev = prev.rank(ascending=False)
    # 当日无数据的行业排名置底（fillna 后 rank_change=0 -> 同步），
    # 避免 int(nan) 崩溃（回归：最新一期缺数行业会中断整条 quant 流水线）。
    rank_latest = rank_latest.fillna(float(len(returns.columns)))
    rank_prev = rank_prev.fillna(float(len(returns.columns)))
    rank_change = rank_prev - rank_latest  # >0 表示排名上升
    thr = params.get("rank_change_threshold", 3)
    lead_lag = np.where(
        rank_change >= thr, "领涨",
        np.where(rank_change <= -thr, "滞后", "同步"),
    )
    amt = amounts.iloc[-1]
    share = amt / amt.sum() if amt.sum() and amt.sum() > 0 else pd.Series(0.0, index=amt.index)
    run_date = _fmt_date(returns.index[-1])
    return pd.DataFrame({
        "run_date": run_date,
        "industry": list(returns.columns),
        "rank": [int(r) for r in rank_latest.values],
        "lead_lag": list(lead_lag),
        "turnover_share": [float(v) for v in share.values],
    })


def _fmt_date(d) -> str:
    if hasattr(d, "date"):
        return str(d.date())
    return str(d)