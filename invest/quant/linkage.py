"""联动网络（短线轨 v1，行业级）：滚动相关 + 领涨判定。"""
from __future__ import annotations

import pandas as pd


def compute_linkage(
    returns: pd.DataFrame,
    corr_window: int = 60,
    corr_threshold: float = 0.7,
) -> pd.DataFrame:
    """returns: date×industry。返回相关对（corr >= 阈值），lead 为近 5 日累计收益更高者。"""
    if returns.empty or len(returns) < corr_window:
        return pd.DataFrame(columns=["run_date", "a", "b", "corr", "lead"])
    rets = returns.tail(corr_window).dropna(how="all")
    corr = rets.corr()
    run_date = _fmt_date(returns.index[-1])
    recent = returns.tail(5).sum()
    rows = []
    cols = list(corr.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            c = corr.loc[a, b]
            if pd.notna(c) and c >= corr_threshold:
                lead = a if recent.get(a, 0.0) >= recent.get(b, 0.0) else b
                rows.append({
                    "run_date": run_date,
                    "a": a,
                    "b": b,
                    "corr": round(float(c), 4),
                    "lead": lead,
                })
    return pd.DataFrame(rows)


def _fmt_date(d) -> str:
    if hasattr(d, "date"):
        return str(d.date())
    return str(d)