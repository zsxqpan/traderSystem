"""拥挤度状态机（TODO 2.2，2026-08-15）：替代一票降级。

状态判定（基于拥挤度分位 + 趋势）：
- 正常:      crowding < 0.7；
- 升温:      0.7 <= crowding < 0.85；
- 高拥挤:    0.85 <= crowding < 0.95；
- 极端但健康: crowding >= 0.95 且 成交占比仍上行（延续放量）；
- 极端且恶化: crowding >= 0.95 且 成交占比回落（量能见顶）→ 一票降级。

crowding 输入为 0-1 分位（compute_crowding 输出），trend 为成交占比变化方向。
"""
from __future__ import annotations

import pandas as pd

# 阈值
WARM = 0.70      # 升温
HOT = 0.85       # 高拥挤
EXTREME = 0.95   # 极端


def crowding_state(
    crowding_pct: float,
    share_trend: float = 0.0,
    warm: float = WARM,
    hot: float = HOT,
    extreme: float = EXTREME,
) -> str:
    """单标的拥挤度状态机。

    crowding_pct: 0-1 分位（rolling 分位）；share_trend: 成交占比近 5 日变化（>0 上行）。
    """
    if crowding_pct >= extreme:
        # 极端分两种：量能仍上行=健康（加速赶顶但未破位），回落=恶化（一票降级）
        return "极端且恶化" if share_trend <= 0 else "极端但健康"
    if crowding_pct >= hot:
        return "高拥挤"
    if crowding_pct >= warm:
        return "升温"
    return "正常"


def state_matrix(
    crowding: pd.DataFrame,
    amounts: pd.DataFrame,
    trend_window: int = 5,
    warm: float = WARM,
    hot: float = HOT,
    extreme: float = EXTREME,
) -> pd.DataFrame:
    """批量状态机：对 crowding 表每行输出状态。

    crowding: compute_crowding 输出（run_date/obj/crowding）；
    amounts: date×industry 成交额（用于算占比趋势）。
    返回 DataFrame: run_date/obj/crowding/share_trend/state。
    """
    shares = amounts.div(amounts.sum(axis=1), axis=0)
    rows = []
    for _, r in crowding.iterrows():
        obj = r["obj"]
        c = float(r["crowding"]) if pd.notna(r["crowding"]) else 0.0
        col = shares.get(obj) if obj in shares.columns else None
        trend = 0.0
        if col is not None:
            s = col.dropna()
            if len(s) > trend_window:
                trend = float(s.iloc[-1] / s.iloc[-1 - trend_window] - 1)
        rows.append({
            "run_date": r["run_date"],
            "obj": obj,
            "crowding": round(c, 4),
            "share_trend": round(trend, 4),
            "state": crowding_state(c, trend, warm, hot, extreme),
        })
    return pd.DataFrame(rows)


def downgrade_ones(rows: pd.DataFrame) -> list[str]:
    """返回需要一票降级的标的（状态=极端且恶化）。"""
    if rows.empty:
        return []
    return rows.loc[rows["state"] == "极端且恶化", "obj"].tolist()
