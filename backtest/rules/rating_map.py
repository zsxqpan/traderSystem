"""评级-仓位映射校准：历史 市场状态×宏观环境 组合的前向收益，输出建议映射。"""
from __future__ import annotations

import json

import pandas as pd


def build_regimes(index_close: pd.Series, macro_df: pd.DataFrame) -> pd.DataFrame:
    """重建每日 市场状态(进攻/中性/防守) × 宏观环境(宽松/中性/收紧)。

    - 市场状态：沪深300 与 MA20/MA60 的关系
    - 宏观环境：M1-M2 剪刀差（>=0 宽松，<-2 收紧）
    """
    idx = index_close.dropna().copy()
    ma20 = idx.rolling(20).mean()
    ma60 = idx.rolling(60).mean()
    state = pd.Series("中性", index=idx.index, dtype=object)
    state[(idx > ma20) & (ma20 > ma60)] = "进攻"
    state[idx < ma60] = "防守"

    def _norm(series: pd.Series) -> pd.Series:
        idx = series.index.astype(str)
        idx = idx.str.replace("年", "-", regex=False)
        idx = idx.str.replace("月份", "", regex=False).str.replace("月", "", regex=False)
        return pd.Series(series.to_numpy(), index=pd.to_datetime(idx, errors="coerce")).dropna()

    m1 = _norm(macro_df[macro_df["indicator"] == "货币(M1)-同比增长"].set_index("date")["value"])
    m2 = _norm(macro_df[macro_df["indicator"] == "货币和准货币(M2)-同比增长"].set_index("date")["value"])
    scissors = (m1 - m2).dropna()
    scissors = scissors.reindex(idx.index, method="ffill")
    regime = pd.Series("中性", index=idx.index, dtype=object)
    regime[scissors >= 0] = "宽松"
    regime[scissors < -2] = "收紧"

    out = pd.DataFrame({"close": idx, "market": state, "macro": regime})
    return out.dropna(subset=["macro"])


def run_rating_map_backtest(
    index_close: pd.Series,
    macro_df: pd.DataFrame,
    horizons: tuple[int, ...] = (10, 20),
) -> dict:
    df = build_regimes(index_close, macro_df)
    if df.empty:
        return {"stats": pd.DataFrame(), "suggestion": {}, "n_cells": 0}
    for h in horizons:
        df[f"fwd{h}"] = index_close.shift(-h) / index_close - 1

    rows = []
    for (m, mc), g in df.groupby(["market", "macro"]):
        for h in horizons:
            vals = g[f"fwd{h}"].dropna()
            if len(vals) < 10:
                continue
            rows.append({
                "market": m, "macro": mc, "horizon": f"fwd{h}",
                "n": int(len(vals)),
                "mean": round(float(vals.mean()), 5),
                "win_rate": round(float((vals > 0).mean()), 4),
            })
    stats = pd.DataFrame(rows)

    # 建议映射：fwd20 平均收益线性映射到 0.05-0.80
    suggestion: dict = {}
    p20 = stats[stats["horizon"] == "fwd20"].copy() if not stats.empty else pd.DataFrame()
    if not p20.empty:
        lo, hi = 0.05, 0.80
        mn, mx = float(p20["mean"].min()), float(p20["mean"].max())
        span = mx - mn
        for _, r in p20.iterrows():
            norm = (r["mean"] - mn) / span if span > 0 else 0.5
            suggestion[f"{r['market']}/{r['macro']}"] = round(lo + norm * (hi - lo), 2)
    return {"stats": stats, "suggestion": suggestion, "n_cells": len(suggestion)}


def metrics_json(result: dict) -> str:
    return json.dumps({
        "suggestion": result["suggestion"],
        "n_cells": result["n_cells"],
        "stats": result["stats"].to_dict(orient="records"),
    }, ensure_ascii=False)