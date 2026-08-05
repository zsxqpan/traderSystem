"""拥挤度（中线轨）：行业成交额占比的滚动分位。

换手率分位待换手率数据源（见 TODO.md），v1 仅用成交占比。
"""
from __future__ import annotations

import pandas as pd

from .indicators import get_params


def compute_crowding(
    amounts: pd.DataFrame,
    params: dict | None = None,
) -> pd.DataFrame:
    """amounts: date×industry。返回 quant_valuation 行（pe_pct/pb_pct 暂为 NULL）。"""
    params = params or get_params("crowding")
    window = params.get("window", 250)
    shares = amounts.div(amounts.sum(axis=1), axis=0)
    rows = []
    for name, s in shares.items():
        hist = s.dropna()
        if len(hist) < 20:
            continue
        win = hist.tail(window)
        pct = float((win <= win.iloc[-1]).mean())
        rows.append({
            "run_date": _fmt_date(hist.index[-1]),
            "obj": name,
            "pe_pct": None,
            "pb_pct": None,
            "crowding": round(pct, 4),
        })
    return pd.DataFrame(rows)


def _fmt_date(d) -> str:
    if hasattr(d, "date"):
        return str(d.date())
    return str(d)