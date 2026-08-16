"""凯利启用条件与置信下界仓位（v3 11.6 / TODO 阶段3，2026-08-15）。

- wilson_lower(): Wilson 95% 置信下界（胜率的下界估计，防小样本高估）；
- kelly_fraction(): 标准凯利 f* = p - (1-p)/b（b=赔率 odds）；
- kelly_capped(): 凯利 × 1/6 系数（保守，防过拟合）；
- kelly_decision(): 格子决策——样本 >= min_n 且 Wilson 下界为正才启用凯利，
  否则回退固定风险（不填假设胜率，v3 11.6）。
"""
from __future__ import annotations

import math
import sqlite3

Z_95 = 1.96  # 正态 95% 分位


def wilson_lower(n: int, wins: int, z: float = Z_95) -> float:
    """Wilson 95% 置信区间下界。

    p_hat = wins/n；下界 = (p_hat + z²/2n - z*sqrt((p_hat(1-p_hat)+z²/4n)/n)) / (1+z²/n)。
    n=0 返回 0。用于胜率的保守估计（小样本防高估）。
    """
    if n <= 0:
        return 0.0
    p_hat = wins / n
    z2 = z * z
    denom = 1 + z2 / n
    center = p_hat + z2 / (2 * n)
    margin = z * math.sqrt((p_hat * (1 - p_hat) + z2 / (4 * n)) / n)
    return max(0.0, (center - margin) / denom)


def kelly_fraction(p: float, odds: float) -> float:
    """标准凯利：f* = p - (1-p)/b（b=赔率，即盈利时收益/亏损时损失）。

    p <= 1/(1+b) 时返回 0（无正期望不下注）。
    """
    if odds <= 0:
        return 0.0
    f = p - (1 - p) / odds
    return max(0.0, f)


def kelly_capped(p: float, odds: float, cap_factor: float = 1 / 6) -> float:
    """保守凯利：标准凯利 × 1/6 系数（防过拟合/参数误差放大）。"""
    return kelly_fraction(p, odds) * cap_factor


def kelly_decision(
    n: int,
    wins: int,
    odds: float,
    min_n: int = 20,
    cap_factor: float = 1 / 6,
    fixed_fraction: float = 0.10,
) -> dict:
    """格子决策：是否启用置信下界凯利。

    规则（v3 11.6）：
    - 样本 < min_n（20 笔）：不启用，回退固定风险；
    - Wilson 95% 下界 <= 0：无正期望证据，回退固定风险；
    - 否则：仓位 = 凯利(Wilson 下界, odds) × 1/6。

    返回 {enabled, fraction, wilson_lower, kelly_raw, reason}。
    """
    if n < min_n:
        return {
            "enabled": False,
            "fraction": fixed_fraction,
            "wilson_lower": round(wilson_lower(n, wins), 4),
            "kelly_raw": 0.0,
            "reason": f"样本不足（{n}<{min_n} 笔）：回退固定风险 {fixed_fraction:.0%}",
        }
    wl = wilson_lower(n, wins)
    kelly_raw = kelly_fraction(wl, odds)
    if wl <= 0 or kelly_raw <= 0:
        return {
            "enabled": False,
            "fraction": fixed_fraction,
            "wilson_lower": round(wl, 4),
            "kelly_raw": round(kelly_raw, 4),
            "reason": f"无正期望证据（Wilson 下界 {wl:.1%}，凯利 {kelly_raw:.1%}）：回退固定风险 {fixed_fraction:.0%}",
        }
    capped = kelly_capped(wl, odds, cap_factor)
    return {
        "enabled": True,
        "fraction": round(capped, 4),
        "wilson_lower": round(wl, 4),
        "kelly_raw": round(kelly_raw, 4),
        "reason": (
            f"启用置信下界凯利：Wilson 下界 {wl:.1%}，标准凯利 {kelly_raw:.1%}，"
            f"×{cap_factor:.4f} = {capped:.1%}"
        ),
    }


def grid_key(cycle: str, level: str, rule_version: str = "") -> str:
    """格子主键：周期×等级×规则版本。"""
    return f"{cycle}|{level}|{rule_version}"


def evaluate_grid(
    conn: sqlite3.Connection,
    cycle: str,
    level: str,
    rule_version: str = "",
    min_n: int = 20,
    fixed_fraction: float = 0.10,
) -> dict:
    """从 trade_records 统计格子样本（按 plan 关联 level/周期）并做凯利决策。

    说明：trade_records 尚无周期/等级列，v1 用 trade_plans 的 symbol 关联
    candidate_pool 的 level 近似；周期字段预留（cycle 参数直接传入）。
    返回 {key, n, wins, decision}。
    """
    key = grid_key(cycle, level, rule_version)
    # 找该 level 的所有 symbol（candidate_pool）
    rows = conn.execute(
        "SELECT symbol FROM candidate_pool WHERE level=?", (level,)
    ).fetchall()
    symbols = [r["symbol"] for r in rows]
    if not symbols:
        return {"key": key, "n": 0, "wins": 0,
                "decision": kelly_decision(0, 0, 1.0, min_n, fixed_fraction=fixed_fraction)}
    marks = ",".join("?" * len(symbols))
    # 从 trade_records 取这些 symbol 的 pnl 记录（关联 plan）
    recs = conn.execute(
        f"""SELECT tr.pnl FROM trade_records tr
            JOIN trade_plans tp ON tr.plan_id = tp.id
            WHERE tp.symbol IN ({marks}) AND tr.pnl IS NOT NULL""",
        tuple(symbols),
    ).fetchall()
    pnls = [float(r["pnl"]) for r in recs]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    # 赔率：平均盈利 / |平均亏损|（无亏损样本时用 1.0 保守）
    gains = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    if gains and losses:
        odds = (sum(gains) / len(gains)) / abs(sum(losses) / len(losses))
    else:
        odds = 1.0
    return {
        "key": key,
        "n": n,
        "wins": wins,
        "odds": round(odds, 3),
        "decision": kelly_decision(n, wins, odds, min_n, fixed_fraction=fixed_fraction),
    }
