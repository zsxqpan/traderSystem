"""Alpha158 核心量价因子子集（2026-08-16，纯 pandas 实现，不依赖 qlib）。

源自 microsoft/qlib 的 Alpha158 因子集（158 个因子），本模块实现其中
**量价核心子集**（约 40 个），适配本系统 daily_bars（symbol/date/OHLCV/amount）
与 index_bars（市场基准）数据结构。输出 date × symbol 的因子截面，
与 backtest/factor_eval.py（IC/ICIR/分组单调）直接兼容。

因子分组：
- KBAR：价格/成交量原始值与其缩放、移位（含 vwap 构造）；
- ROC：多周期收益率（close 与 vwap 的 1/2/3/5/10/20 日动量）；
- MA：多周期均线（close/vwap 的 5/10/20/30/60 日均线值）；
- STD：多周期波动（close 的 5/10/20/30/60 日标准差）；
- BETA：对市场基准的多周期回归 Beta；
- RESI：对市场基准回归后的残差（多周期）；
- MAX/MIN：多周期最高/最低（close 的 5/10/20/30/60 日滚动极值）；
- QTL：多周期分位数（close 的 20/40/60 日 10%/20%/80%/90% 分位）。

用法：
    from invest.quant.alpha158 import compute_alpha158, factor_names
    factor_df, names = compute_alpha158(daily, index_daily)   # date×symbol 截面
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 因子窗口配置
_WINDOWS = (5, 10, 20, 30, 60)
_ROC_WINDOWS = (1, 2, 3, 5, 10, 20)


def _add_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """构造 vwap（成交额/成交量，量能加权均价）。"""
    out = df.copy()
    vol = out["volume"].replace(0, np.nan)
    out["vwap"] = (out["amount"] / vol).clip(lower=0)
    # amount 缺失时用 (H+L+C)/3 近似
    out["vwap"] = out["vwap"].fillna((out["high"] + out["low"] + out["close"]) / 3)
    return out


def _rolling_rank(x: pd.Series, window: int) -> pd.Series:
    """滚动分位：当前值在过去 window 天中的分位（0-1）。"""
    return x.rolling(window, min_periods=3).apply(
        lambda a: float((a <= a[-1]).mean()), raw=True
    )


def compute_alpha158(
    daily: pd.DataFrame,
    index_daily: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """计算 Alpha158 核心因子。

    daily: date/symbol/open/high/low/close/volume/amount（长表）；
    index_daily: date/index_code/close（市场基准，用于 BETA/RESI，可空）。

    返回 (factor_df, names)：factor_df 为 date×symbol 截面（MultiIndex 扁平化），
    列名如 KBAR_CLOSE0、ROC_5、MA_20、STD_20、BETA_20、RESI_20、MAX_20、QTL_20_80。
    """
    if daily is None or daily.empty:
        return pd.DataFrame(), []
    df = daily.copy()
    df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    df = df.dropna(subset=["date", "symbol"])
    df = _add_vwap(df)
    df = df.sort_values(["symbol", "date"])

    # 市场基准收益（用于 BETA/RESI）
    bench_ret: pd.Series | None = None
    if index_daily is not None and not index_daily.empty:
        b = index_daily.copy()
        b["date"] = pd.to_datetime(b["date"], format="mixed", errors="coerce")
        b = b.sort_values("date")
        bench = b.set_index("date")["close"].pct_change()
        bench_ret = bench.rename("bench")

    names: list[str] = []
    frames: dict[str, pd.DataFrame] = {}

    for sym, g in df.groupby("symbol", sort=False):
        g = g.set_index("date").sort_index()
        cols: dict[str, pd.Series] = {}
        close = g["close"]
        vwap = g["vwap"]
        vol = g["volume"]

        # KBAR：原始值 + 缩放 + 移位
        cols["KBAR_CLOSE0"] = close
        cols["KBAR_OPEN0"] = g["open"]
        cols["KBAR_HIGH0"] = g["high"]
        cols["KBAR_LOW0"] = g["low"]
        cols["KBAR_VOLUME0"] = vol
        cols["KBAR_VWAP0"] = vwap
        for w in (1, 5, 10):
            cols[f"KBAR_CLOSE{w}"] = close.shift(w)
            cols[f"KBAR_VWAP{w}"] = vwap.shift(w)

        # ROC：多周期动量
        for w in _ROC_WINDOWS:
            cols[f"ROC_{w}"] = close.pct_change(w)
            cols[f"ROC_VWAP_{w}"] = vwap.pct_change(w)

        # MA：多周期均线
        for w in _WINDOWS:
            cols[f"MA_{w}"] = close.rolling(w, min_periods=2).mean()
            cols[f"MA_VWAP_{w}"] = vwap.rolling(w, min_periods=2).mean()

        # STD：多周期波动
        for w in _WINDOWS:
            cols[f"STD_{w}"] = close.rolling(w, min_periods=2).std()

        # BETA / RESI：对市场基准
        if bench_ret is not None:
            sym_ret = close.pct_change()
            joint = pd.concat([sym_ret.rename("sym"), bench_ret], axis=1).dropna()
            for w in _WINDOWS:
                beta = joint["sym"].rolling(w, min_periods=5).cov(joint["bench"]) / (
                    joint["bench"].rolling(w, min_periods=5).var() + 1e-12
                )
                cols[f"BETA_{w}"] = beta
                # 残差 = 收益 - beta*bench
                alpha = joint["sym"].rolling(w, min_periods=5).mean() - beta * joint["bench"].rolling(
                    w, min_periods=5
                ).mean()
                resi = joint["sym"] - (beta * joint["bench"] + alpha)
                cols[f"RESI_{w}"] = resi
        else:
            for w in _WINDOWS:
                cols[f"BETA_{w}"] = pd.Series(np.nan, index=g.index)
                cols[f"RESI_{w}"] = pd.Series(np.nan, index=g.index)

        # MAX / MIN：多周期极值
        for w in _WINDOWS:
            cols[f"MAX_{w}"] = close.rolling(w, min_periods=2).max()
            cols[f"MIN_{w}"] = close.rolling(w, min_periods=2).min()

        # QTL：多周期分位
        for w in (20, 40, 60):
            for q in (10, 20, 80, 90):
                cols[f"QTL_{w}_{q}"] = close.rolling(w, min_periods=5).quantile(q / 100)

        # 滚动分位（当前价在窗口内位置，0-1）
        for w in (20, 60):
            cols[f"RANK_{w}"] = _rolling_rank(close, w)

        sub = pd.DataFrame(cols, index=g.index)
        sub["symbol"] = sym
        frames[sym] = sub

    if not frames:
        return pd.DataFrame(), []
    allf = pd.concat(frames.values())
    allf = allf.reset_index()
    allf = allf.rename(columns={"index": "date"})
    # 透视成 date×symbol
    pivot_cols = [c for c in allf.columns if c not in ("date", "symbol")]
    names = sorted(pivot_cols)
    if not names:
        return pd.DataFrame(), []
    series = {}
    for col in names:
        p = allf.pivot_table(index="date", columns="symbol", values=col, aggfunc="first", dropna=False)
        series[col] = p
    # 合并成 MultiIndex 列 (factor, symbol)
    factor_df = pd.concat(series.values(), axis=1, keys=series.keys())
    return factor_df, names


def factor_names() -> list[str]:
    """返回本实现会产出的全部因子名（供测试/文档用）。"""
    names: list[str] = []
    for w in (1, 5, 10):
        names += [f"KBAR_CLOSE{w}", f"KBAR_VWAP{w}"]
    for w in _ROC_WINDOWS:
        names += [f"ROC_{w}", f"ROC_VWAP_{w}"]
    for w in _WINDOWS:
        names += [f"MA_{w}", f"MA_VWAP_{w}", f"STD_{w}", f"BETA_{w}", f"RESI_{w}", f"MAX_{w}", f"MIN_{w}"]
    for w in (20, 40, 60):
        for q in (10, 20, 80, 90):
            names.append(f"QTL_{w}_{q}")
    names += ["KBAR_CLOSE0", "KBAR_OPEN0", "KBAR_HIGH0", "KBAR_LOW0", "KBAR_VOLUME0", "KBAR_VWAP0"]
    names += ["RANK_20", "RANK_60"]
    return sorted(set(names))
