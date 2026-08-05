"""估值分位（中线轨）：行业 PE 历史分位。PB 待数据源（见 TODO.md）。"""
from __future__ import annotations

import pandas as pd


def compute_pe_percentile(history: pd.DataFrame) -> pd.DataFrame:
    """history: date/industry/pe。返回每行业最新 PE 的历史分位（0-1）。"""
    if history is None or history.empty:
        return pd.DataFrame(columns=["industry", "pe", "pe_pct"])
    h = history.copy()
    h["pe"] = pd.to_numeric(h["pe"], errors="coerce")
    h = h.dropna(subset=["pe"])
    if h.empty:
        return pd.DataFrame(columns=["industry", "pe", "pe_pct"])
    h["date"] = h["date"].astype(str)
    latest_date = h["date"].max()
    rows = []
    for ind, g in h.groupby("industry"):
        g = g.sort_values("date")
        latest = g[g["date"] == latest_date]
        if latest.empty:
            latest = g.tail(1)
        cur = float(latest["pe"].iloc[0])
        pct = float((g["pe"] <= cur).mean())
        rows.append({"industry": ind, "pe": cur, "pe_pct": round(pct, 4)})
    return pd.DataFrame(rows)


def merge_valuation(
    existing: pd.DataFrame,
    percentiles: pd.DataFrame,
) -> pd.DataFrame:
    """把 pe_pct 合并进 quant_valuation 快照（保留 crowding）。"""
    if existing is None or existing.empty:
        return existing
    out = existing.copy()
    pct_map = dict(zip(percentiles["industry"], percentiles["pe_pct"]))
    out["pe_pct"] = out["obj"].map(pct_map)
    return out