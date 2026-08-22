"""估值分位（中线轨）：行业 PE/PB 历史分位（[A]1，2026-08-15）。

PE 数据源：巨潮行业估值（industry_valuation.pe，已接入）；
PB 数据源：乐咕乐股/东财行业估值（industry_valuation.pb，待数据源接入，
代码与入库已就绪：compute_pb_percentile + merge_valuation 自动合并 pb_pct）。
"""
from __future__ import annotations

import pandas as pd


def _percentile_rows(history: pd.DataFrame, column: str, col_pct: str) -> pd.DataFrame:
    """通用：按行业算最新 {column} 的历史分位。"""
    if history is None or history.empty:
        return pd.DataFrame(columns=["industry", column, col_pct])
    h = history.copy()
    h[column] = pd.to_numeric(h[column], errors="coerce")
    h = h.dropna(subset=[column])
    if h.empty:
        return pd.DataFrame(columns=["industry", column, col_pct])
    h["date"] = h["date"].astype(str)
    latest_date = h["date"].max()
    rows = []
    for ind, g in h.groupby("industry"):
        g = g.sort_values("date")
        latest = g[g["date"] == latest_date]
        if latest.empty:
            latest = g.tail(1)
        cur = float(latest[column].iloc[0])
        pct = float((g[column] <= cur).mean())
        rows.append({"industry": ind, column: cur, col_pct: round(pct, 4)})
    return pd.DataFrame(rows)


def compute_pe_percentile(history: pd.DataFrame) -> pd.DataFrame:
    """history: date/industry/pe。返回每行业最新 PE 的历史分位（0-1）。"""
    return _percentile_rows(history, "pe", "pe_pct")


def compute_pb_percentile(history: pd.DataFrame) -> pd.DataFrame:
    """history: date/industry/pb。返回每行业最新 PB 的历史分位（0-1）。"""
    return _percentile_rows(history, "pb", "pb_pct")


def merge_valuation(
    existing: pd.DataFrame,
    percentiles: pd.DataFrame,
    col_pct: str = "pe_pct",
) -> pd.DataFrame:
    """把 pe_pct/pb_pct 合并进 quant_valuation 快照（保留 crowding）。"""
    if existing is None or existing.empty:
        return existing
    out = existing.copy()
    pct_map = dict(zip(percentiles["industry"], percentiles[col_pct]))
    out[col_pct] = out["obj"].map(pct_map)
    return out
