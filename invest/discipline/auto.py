"""因子与价差计算自动化（TODO [A]11/[A]12，2026-08-15）。

- 四套周期镜像 registry（[A]12）：波段/配置/事件博弈/趋势 四套周期镜像，
  各自带参数（参照年数、错价阈值、最大持有），一键全量启用；
- 自动化（[A]11）：对候选池每个标的自动计算
  1) 主价差（价格分位/Z/锚区间，结构断点截断后）；
  2) 行业 PE 价差（如有 industry_valuation）；
  3) 因子打分：把客观信号映射到 0-5 分因子（低估度/景气趋势/流动性/拥挤度）；
  4) 输出自动打分报告，供人工建卡（不自动入池，防选择偏差）。
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from invest.discipline.spread import (
    factor_score,
    industry_pe_spread,
    price_spread,
)

# 四套周期镜像（[A]12）：波段/配置/事件博弈/趋势
CYCLE_MIRRORS: dict[str, dict] = {
    "波段": {
        "years": 3,
        "cheap_pct": 0.35,      # 波段看重相对低分位
        "max_hold_days": 40,
        "note": "3 年窗口，历史分位 <35% 视为错价候选",
    },
    "配置": {
        "years": 5,
        "cheap_pct": 0.30,      # 配置看重中长期低分位
        "max_hold_days": 120,
        "note": "5 年窗口，历史分位 <30% 视为错价候选",
    },
    "事件博弈": {
        "years": 1,
        "cheap_pct": 0.45,      # 事件驱动不苛求分位，更看重催化与修复空间
        "max_hold_days": 30,
        "note": "1 年窗口，分位要求放宽至 <45%（催化优先）",
    },
    "趋势": {
        "years": 2,
        "cheap_pct": 0.50,      # 趋势跟随不要求便宜，只求主价差可计算
        "max_hold_days": 60,
        "note": "2 年窗口，趋势周期不对分位设硬门槛",
    },
}


def cycle_mirror_params(cycle: str) -> dict:
    """周期镜像参数（未知周期回落波段默认）。"""
    return CYCLE_MIRRORS.get(cycle, CYCLE_MIRRORS["波段"])


def auto_price_factors(close: pd.Series) -> dict:
    """把价格信号映射为因子分（0-5）。

    低估度：最新价历史分位越低分越高（分位<0.3 → 5 分线性）；
    趋势：20 日均线斜率方向给分（修复因子）；
    波动：滚动 20 日年化波动，越低风险过滤分越高。
    """
    s = close.dropna()
    factors: list[dict] = []
    if len(s) >= 40:
        pct = float((s <= s.iloc[-1]).mean())
        misprice = max(0.0, min(5.0, 5.0 * (1 - pct / 0.3))) if pct < 0.3 else 0.0
        factors.append({"name": "价格低估度", "score": round(misprice, 2), "role": "错价"})
        ma20 = s.rolling(20).mean()
        slope = (ma20.iloc[-1] / ma20.iloc[-21] - 1) if len(ma20) >= 21 and ma20.iloc[-21] else 0.0
        trend = max(0.0, min(5.0, 5.0 * (1 + slope / 0.05)))  # 斜率高 → 修复分高
        factors.append({"name": "趋势修复", "score": round(trend, 2), "role": "修复"})
        ret = s.pct_change().tail(20)
        vol = float(ret.std() * (252 ** 0.5)) if len(ret) >= 5 and ret.std() else 0.0
        risk = max(0.0, min(5.0, 5.0 * (1 - vol / 0.6)))  # 波动低 → 风险过滤分高
        factors.append({"name": "波动风险", "score": round(risk, 2), "role": "风险过滤"})
    return {"factors": factors, "ok": bool(factors)}


def _close_series(
    conn: sqlite3.Connection,
    symbol: str,
    years: int = 3,
    as_of: str | None = None,
) -> pd.Series:
    """取个股近 years 年收盘价序列（升序）。as_of 有值时截断到该日。"""
    import datetime as dt
    if as_of:
        rows = conn.execute(
            """SELECT date, close FROM daily_bars WHERE symbol=?
               AND close IS NOT NULL AND date<=? ORDER BY date""",
            (symbol, as_of),
        ).fetchall()
        end = pd.Timestamp(as_of)
    else:
        rows = conn.execute(
            """SELECT date, close FROM daily_bars WHERE symbol=?
               AND close IS NOT NULL ORDER BY date""",
            (symbol,),
        ).fetchall()
        end = pd.Timestamp.now()
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    cutoff = end - dt.timedelta(days=365 * years)
    s = df[df["date"] >= cutoff]["close"]
    return s.reset_index(drop=True)


def auto_factor_score(
    conn: sqlite3.Connection,
    symbol: str,
    cycle: str = "波段",
    spread: dict | None = None,
    as_of: str | None = None,
) -> dict:
    """自动化因子打分：价格信号 + 主价差（传入或自动算）→ 综合分。

    返回 {ok, symbol, cycle, spread, factors, factor_result, eligible}。
    """
    params = cycle_mirror_params(cycle)
    if spread is None:
        spread = price_spread(conn, symbol, years=params["years"], as_of=as_of)
    f = auto_price_factors(_close_series(conn, symbol, years=params["years"], as_of=as_of))
    # 主价差并入错价因子（若分位极低则加分）
    factors = list(f["factors"])
    if spread.get("ok") and spread.get("pct_rank") is not None:
        pct = spread["pct_rank"]
        score = max(0.0, min(5.0, 5.0 * (1 - pct / 0.3))) if pct < 0.3 else 0.0
        factors.append({"name": "主价差低估", "score": round(score, 2), "role": "错价"})
    if not factors:
        return {"ok": False, "symbol": symbol, "cycle": cycle, "note": "无可用信号"}
    result = factor_score(factors)
    cheap_pct = params["cheap_pct"]
    pct = spread.get("pct_rank") if spread.get("ok") else None
    eligible = pct is not None and pct < cheap_pct
    return {
        "ok": True,
        "symbol": symbol,
        "cycle": cycle,
        "spread": {k: spread[k] for k in ("ok", "current", "median", "pct_rank", "z_score", "anchor_range") if k in spread},
        "factors": result["per_factor"],
        "factor_result": result,
        "eligible": bool(eligible),
        "cheap_pct": cheap_pct,
        "note": f"{cycle}镜像：{'具备错价必要条件' if eligible else '未达错价必要条件（分位偏高或不可用）'}",
    }


def run_pool_automation(
    conn: sqlite3.Connection,
    cycles: list[str] | None = None,
) -> dict:
    """对候选池全部标的自动计算四套周期镜像打分（[A]11+[A]12）。

    cycles: 默认全部四套；返回 {cycle: {symbol: report}, summary}。
    只输出报告，不自动入池/建卡（防选择偏差，v3 3.2）。
    """
    cycles = cycles or list(CYCLE_MIRRORS.keys())
    pool = conn.execute(
        "SELECT symbol, industry FROM candidate_pool WHERE out_date IS NULL ORDER BY level, in_date"
    ).fetchall()
    results: dict[str, dict] = {c: {} for c in cycles}
    for r in pool:
        symbol = r["symbol"]
        industry = r["industry"] or ""
        for cycle in cycles:
            spread = price_spread(conn, symbol, years=cycle_mirror_params(cycle)["years"])
            if not spread.get("ok") and industry:
                spread = industry_pe_spread(conn, industry, years=cycle_mirror_params(cycle)["years"])
            report = auto_factor_score(conn, symbol, cycle=cycle, spread=spread)
            results[cycle][symbol] = report
    summary = {}
    for cycle, subs in results.items():
        n_eligible = sum(1 for rep in subs.values() if rep.get("eligible"))
        summary[cycle] = {"n_pool": len(subs), "n_eligible": n_eligible}
    return {"cycles": cycles, "results": results, "summary": summary}
