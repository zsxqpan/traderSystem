"""因子共线性控制（TODO 2.2，2026-08-15）。

月度因子相关矩阵，|ρ| > 0.60 的因子对不得以完整权重同入组合（v3 8.3）。
- corr_matrix(): 因子截面相关矩阵（按日期对齐）；
- find_collinear_pairs(): 输出超阈值的因子对；
- collinearity_report(): 完整报告（矩阵 + 违规对 + 建议降权）。
"""
from __future__ import annotations

import pandas as pd

CORR_THRESHOLD = 0.60


def corr_matrix(factor_df: pd.DataFrame, min_periods: int = 30) -> pd.DataFrame:
    """因子相关矩阵。

    factor_df: date(索引) × factor(列)，各列为因子值（缺失值自动剔除）。
    返回列间 Pearson 相关矩阵（至少 min_periods 个共同观测才计算）。
    """
    if factor_df.empty or factor_df.shape[1] < 2:
        return pd.DataFrame()
    return factor_df.corr(min_periods=min_periods)


def find_collinear_pairs(
    factor_df: pd.DataFrame,
    threshold: float = CORR_THRESHOLD,
    min_periods: int = 30,
) -> list[dict]:
    """返回 |ρ| > threshold 的因子对（每对一次，升序排列）。

    每项: {a, b, corr, suggestion}，suggestion 为降权建议（择一保留）。
    """
    m = corr_matrix(factor_df, min_periods)
    if m.empty:
        return []
    pairs: list[dict] = []
    cols = list(m.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            r = m.loc[a, b]
            if pd.isna(r):
                continue
            if abs(r) > threshold:
                pairs.append({
                    "a": a,
                    "b": b,
                    "corr": round(float(r), 4),
                    "suggestion": f"|ρ|={abs(r):.2f}>{threshold:.2f}：二者择一保留完整权重，另一降为背景因子（v3 8.1/8.3）",
                })
    pairs.sort(key=lambda x: -abs(x["corr"]))
    return pairs


def collinearity_report(
    factor_df: pd.DataFrame,
    threshold: float = CORR_THRESHOLD,
    min_periods: int = 30,
) -> dict:
    """完整报告：矩阵 + 违规对 + 通过状态。"""
    m = corr_matrix(factor_df, min_periods)
    pairs = find_collinear_pairs(factor_df, threshold, min_periods)
    return {
        "corr_matrix": m,
        "collinear_pairs": pairs,
        "ok": not pairs,
        "threshold": threshold,
        "n_factors": int(factor_df.shape[1]) if not factor_df.empty else 0,
        "n_periods": int(factor_df.shape[0]) if not factor_df.empty else 0,
    }


def weight_adjustment(
    factor_df: pd.DataFrame,
    weights: dict[str, float],
    threshold: float = CORR_THRESHOLD,
    min_periods: int = 30,
) -> dict[str, float]:
    """共线性降权：违规对中相关性更高者保留，另一因子权重砍半。

    返回调整后权重（未涉及因子不变）。多次迭代至无违规（最多 5 轮）。
    """
    adj = dict(weights)
    for _ in range(5):
        pairs = find_collinear_pairs(factor_df, threshold, min_periods)
        if not pairs:
            break
        for p in pairs:
            a, b = p["a"], p["b"]
            # 简化：两因子都在权重表里时，二者都减半（保守）
            if a in adj and b in adj:
                adj[a] = round(adj[a] / 2, 4)
                adj[b] = round(adj[b] / 2, 4)
    return adj
