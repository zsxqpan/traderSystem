"""资金属性（短线轨 v1）：行业风格标签。

fund_type（机构/游资/量化）依赖龙虎榜席位明细数据，见 TODO.md；
v1 仅从量价形态推断行情风格。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def style_series(returns: pd.Series, close: pd.Series) -> pd.Series:
    """向量化风格序列：主题炒作 / 产业趋势 / 超跌修复 / 困境反转 / 震荡 / 数据不足。"""
    vol = returns.rolling(20).std() * np.sqrt(252)
    mom5 = close.pct_change(5)
    ma60 = close.rolling(60).mean()
    dist = close / ma60 - 1
    out = pd.Series("震荡", index=close.index, dtype=object)
    out[(vol > 0.35) & (mom5.abs() > 0.05)] = "主题炒作"
    out[(mom5 > 0.02) & (vol < 0.30) & (dist > 0)] = "产业趋势"
    out[(dist < -0.15) & (mom5 > 0)] = "超跌修复"
    out[(dist < 0) & (mom5 > 0.02)] = "困境反转"
    out[ma60.isna()] = "数据不足"
    return out


def classify_style(returns: pd.Series, close: pd.Series) -> str:
    """最新一期风格标签（复用 style_series）。"""
    s = close.dropna()
    if len(s) < 60:
        return "未知"
    return str(style_series(returns[s.index], s).iloc[-1])


def compute_capital(closes: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    """closes/returns: date×industry。返回行业 quant_capital 快照行。"""
    rows = []
    for name, close in closes.items():
        style = classify_style(returns[name], close)
        rows.append({
            "run_date": _fmt_date(close.dropna().index[-1]),
            "obj": name,
            "obj_type": "industry",
            "fund_type": None,
            "style": style,
            "confidence": 0.5,
        })
    return pd.DataFrame(rows)


def classify_seat(seat_name: str) -> str | None:
    """营业部名称 → 资金类型：机构 / 北向 / 量化 / 游资。

    榜单占位行（seat_type='list'，龙虎榜名单无席位概念）、空值与 NaN 返回 None，
    不得误分类为游资（2026-08-15 修复：曾把 'list' 落入默认分支变假数据）。
    """
    if not isinstance(seat_name, str):
        return None
    name = seat_name.strip()
    if not name or name == "list":
        return None
    if "机构专用" in name:
        return "机构"
    if "股通" in name:
        return "北向"
    if "量化" in name:
        return "量化"
    return "游资"


def aggregate_fund_types(seat_rows: pd.DataFrame) -> dict[str, dict]:
    """席位明细 → {symbol: {fund_type, confidence}}（按净额主导类型）。

    只统计真实席位行（seat_type 非 list/空）；榜单占位行剔除，防假数据。
    """
    out: dict = {}
    if seat_rows is None or seat_rows.empty or "symbol" not in seat_rows.columns:
        return out
    g = seat_rows.copy()
    g["fund_type"] = g["seat_type"].apply(classify_seat)
    g = g[g["fund_type"].notna()]  # 剔除占位行（list/空）——只能由真实席位得出类型
    if g.empty:
        return out
    for sym, grp in g.groupby("symbol"):
        by_type = grp.groupby("fund_type")["net"].sum()
        total = float(by_type.sum())
        if total == 0 or by_type.empty:
            out[sym] = {"fund_type": None, "confidence": 0.0}
            continue
        dom = by_type.idxmax()
        out[sym] = {"fund_type": dom, "confidence": round(float(by_type[dom] / total), 4)}
    return out


def compute_stock_capital(
    stock_closes: pd.DataFrame,
    stock_returns: pd.DataFrame,
    fund_types: dict | None = None,
) -> pd.DataFrame:
    """个股 quant_capital 行（fund_type 来自席位数据）。"""
    fund_types = fund_types or {}
    rows = []
    for name, close in stock_closes.items():
        style = classify_style(stock_returns[name], close)
        ft = fund_types.get(name, {})
        rows.append({
            "run_date": _fmt_date(close.dropna().index[-1]),
            "obj": name,
            "obj_type": "stock",
            "fund_type": ft.get("fund_type"),
            "style": style,
            "confidence": ft.get("confidence", 0.5 if ft.get("fund_type") else 0.3),
        })
    return pd.DataFrame(rows)


def _fmt_date(d) -> str:
    if hasattr(d, "date"):
        return str(d.date())
    return str(d)