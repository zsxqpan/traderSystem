"""因子有效性检验（v3 8.5，2026-08-15）：滚动 IC、ICIR、分组单调性。

输入：因子截面（date × asset 的因子值）+ 前向收益（date × asset）。
- rolling_ic(): 滚动窗口内因子与未来 N 日收益的截面 Spearman 相关（IC）；
- icir(): IC 均值 / IC 标准差（稳健性度量）；
- group_monotonicity(): 按因子值分 N 组，统计各组平均前向收益与单调性；
- factor_eval_report(): 汇总报告（IC/ICIR/分组单调/结论）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_GROUPS = 5


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """纯 numpy Spearman 相关（rank 后 Pearson），避免 scipy 依赖。"""
    ra = a.rank(method="average")
    rb = b.rank(method="average")
    return float(np.corrcoef(ra, rb)[0, 1]) if len(ra) >= 2 else float("nan")


def _cross_section_ic(factor_row: pd.Series, fwd_row: pd.Series) -> float | None:
    """单期截面 IC：因子与 fwd 收益的 Spearman 相关。"""
    df = pd.concat([factor_row, fwd_row], axis=1, keys=["f", "y"]).dropna()
    if len(df) < 5:
        return None
    return _spearman(df["f"], df["y"])


def rolling_ic(
    factor_df: pd.DataFrame,
    fwd_df: pd.DataFrame,
    window: int = 60,
) -> pd.Series:
    """滚动截面 IC 序列（index=日期，value=当期 Spearman IC）。"""
    dates = factor_df.index.intersection(fwd_df.index)
    ic_values = {}
    for d in dates:
        ic = _cross_section_ic(factor_df.loc[d], fwd_df.loc[d])
        ic_values[d] = ic
    ic = pd.Series(ic_values).dropna()
    return ic.rolling(window, min_periods=20).mean()


def icir(ic: pd.Series, window: int = 60) -> pd.Series:
    """ICIR：滚动 IC 均值 / IC 标准差（信息比率，衡量因子稳定性）。"""
    return ic.rolling(window, min_periods=20).mean() / ic.rolling(window, min_periods=20).std()


def group_monotonicity(
    factor_df: pd.DataFrame,
    fwd_df: pd.DataFrame,
    n_groups: int = DEFAULT_GROUPS,
    min_n: int = 5,
) -> pd.DataFrame:
    """按因子值分 N 组，统计各组前向收益均值/胜率/样本数。

    返回: {group: 1..N, mean_ret, win_rate, n, monotonic_hint}。
    group N 为因子最高组（做多方向），group 1 为最低组。
    """
    rows = []
    dates = factor_df.index.intersection(fwd_df.index)
    # 汇总所有日期：每 (date, asset) 一条记录，按当期因子分位分组
    records = []
    for d in dates:
        f = factor_df.loc[d]
        y = fwd_df.loc[d]
        df = pd.concat([f, y], axis=1, keys=["f", "y"]).dropna()
        if len(df) < min_n:
            continue
        for asset, r in df.iterrows():
            records.append({"date": d, "asset": asset, "f": r["f"], "y": r["y"]})
    if not records:
        return pd.DataFrame(columns=["group", "mean_ret", "win_rate", "n", "monotonic_hint"])
    rec = pd.DataFrame(records)
    # 按日期分组计算因子分位（截面 rank 归一化，避免时间趋势）
    rec["rank_pct"] = rec.groupby("date")["f"].rank(pct=True)
    rec["group"] = np.ceil(rec["rank_pct"] * n_groups).clip(1, n_groups).astype(int)

    out = []
    for g in range(1, n_groups + 1):
        sub = rec[rec["group"] == g]
        if len(sub) < min_n:
            continue
        out.append({
            "group": g,
            "mean_ret": round(float(sub["y"].mean()), 5),
            "win_rate": round(float((sub["y"] > 0).mean()), 4),
            "n": int(len(sub)),
        })
    gdf = pd.DataFrame(out).sort_values("group").reset_index(drop=True)
    if len(gdf) >= 3:
        # 单调性：最高组 vs 最低组，以及组间均值单调趋势（简单线性相关）
        means = gdf["mean_ret"].values
        corr = float(np.corrcoef(np.arange(len(means)), means)[0, 1]) if len(means) >= 2 else 0.0
        spread = float(gdf["mean_ret"].iloc[-1] - gdf["mean_ret"].iloc[0])
        gdf["monotonic_hint"] = (
            "单调（多空价差为正）" if corr > 0.5 and spread > 0
            else ("反向单调（多空价差为负）" if corr < -0.5 and spread < 0 else "不单调")
        )
    else:
        gdf["monotonic_hint"] = "样本不足"
    return gdf


def factor_eval_report(
    factor_df: pd.DataFrame,
    fwd_df: pd.DataFrame,
    window: int = 60,
    n_groups: int = DEFAULT_GROUPS,
) -> dict:
    """因子有效性汇总报告。

    返回: {ic_mean, icir, ic_positive_pct, monotonicity, conclusion}。
    """
    ic = pd.Series({
        d: _cross_section_ic(factor_df.loc[d], fwd_df.loc[d])
        for d in factor_df.index.intersection(fwd_df.index)
    }).dropna()
    if ic.empty:
        return {"ok": False, "note": "无有效截面样本"}
    ic_mean = float(ic.mean())
    ic_std = float(ic.std())
    icir_val = ic_mean / ic_std if ic_std > 0 else 0.0
    ic_positive = float((ic > 0).mean())
    groups = group_monotonicity(factor_df, fwd_df, n_groups)

    # 结论（IC 与 ICIR 双门槛，防噪声因子误判）
    conclusions = []
    if abs(ic_mean) >= 0.03 and icir_val >= 0.3:
        conclusions.append("因子有效（|IC|>=0.03 且 ICIR>=0.3）")
    elif abs(ic_mean) >= 0.02 and icir_val >= 0.2:
        conclusions.append("因子弱有效（0.02<=|IC|<0.03 且 ICIR>=0.2）")
    else:
        conclusions.append("因子无效或需进一步检验（|IC|<0.02 或 ICIR<0.2）")
    if not groups.empty:
        monotonic = groups["monotonic_hint"].iloc[-1]
        if "单调" in monotonic:
            conclusions.append(f"分组{monotonic}")
    return {
        "ok": True,
        "n_periods": int(len(ic)),
        "ic_mean": round(ic_mean, 4),
        "ic_std": round(ic_std, 4),
        "icir": round(icir_val, 3),
        "ic_positive_pct": round(ic_positive, 3),
        "groups": groups,
        "conclusions": conclusions,
    }
