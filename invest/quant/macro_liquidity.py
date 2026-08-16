"""宏观流动性加工（中线轨）：M1-M2 剪刀差、PMI、社融增量、新增信贷。"""
from __future__ import annotations

import pandas as pd


def compute_macro_liquidity(macro_df: pd.DataFrame) -> pd.DataFrame:
    """macro_df: indicator/date/value（宏观长表）。返回 quant_macro 行。"""
    if macro_df is None or macro_df.empty:
        return pd.DataFrame(columns=["date", "indicator", "value"])
    macro_df = macro_df.copy()
    macro_df["date"] = macro_df["date"].astype(str)
    latest = (
        macro_df.sort_values("date")
        .groupby("indicator", as_index=False)
        .tail(1)
    )
    idx = dict(zip(latest["indicator"], latest["value"]))
    rows = []
    last_date = str(latest["date"].max())

    def add(ind: str, val) -> None:
        if val is not None and pd.notna(val):
            rows.append({"date": last_date, "indicator": ind, "value": round(float(val), 4)})

    if "货币(M1)-同比增长" in idx and "货币和准货币(M2)-同比增长" in idx:
        add("M1-M2剪刀差", idx["货币(M1)-同比增长"] - idx["货币和准货币(M2)-同比增长"])
    add("PMI制造业指数", idx.get("制造业-指数"))
    # 真实社融增量（商务部源 macro_shrzgm，2026-08-15 接入）；无则回落新增信贷同比
    add("社融增量", idx.get("社会融资规模增量"))
    add("新增信贷同比", idx.get("当月-同比增长"))
    return pd.DataFrame(rows)