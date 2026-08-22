"""kill-gate 击杀门禁（2026-08-16，参考 crypto-trading-bot-postmortem）。

核心思想（源自 freqtrade 社区反过拟合实践）：策略上线前必须通过一组
**硬性风险门槛**，任一不满足即"击杀"（kill）——不允许带病上线。

门槛（可配置，默认参考 freqtrade 社区常见纪律值）：
1. max_drawdown：最大回撤（复权后峰值到谷底）不超过阈值（默认 20%）；
2. max_consecutive_losses：最大连亏笔数不超过阈值（默认 6 笔）；
3. min_profit_factor：盈利因子（总盈利/总亏损绝对值）不低于阈值（默认 1.2）；
4. min_win_rate：胜率不低于阈值（默认 40%），样本不足时豁免；
5. min_trades：最少交易笔数（默认 20 笔，样本不足直接击杀——数据不支撑结论）。

输入：逐笔交易记录（trade_records 或回测结果），输出 kill-gate 报告。
"""
from __future__ import annotations

import sqlite3

# 默认门槛（freqtrade 社区常用纪律值，可覆盖）
DEFAULT_GATES = {
    "max_drawdown": 0.20,          # 最大回撤 ≤ 20%
    "max_consecutive_losses": 6,   # 最大连亏 ≤ 6 笔
    "min_profit_factor": 1.2,      # 盈利因子 ≥ 1.2
    "min_win_rate": 0.40,          # 胜率 ≥ 40%
    "min_trades": 20,              # 最少样本 ≥ 20 笔
}


def _pnl_series(trades: list[dict]) -> list[float]:
    """从交易记录提取盈亏序列（pnl 字段，按时间排序）。"""
    items = [(t.get("created_at") or "", float(t.get("pnl") or 0.0)) for t in trades]
    items.sort(key=lambda x: x[0])
    return [p for _, p in items]


def _max_drawdown(pnls: list[float]) -> float:
    """最大回撤：峰值到谷底的累计回撤（0-1）。"""
    if not pnls:
        return 0.0
    equity = 0.0
    peak = 0.0
    mdd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak)
    return mdd


def _max_consecutive_losses(pnls: list[float]) -> int:
    """最大连亏笔数。"""
    cur = best = 0
    for p in pnls:
        if p < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _profit_factor(pnls: list[float]) -> float:
    """盈利因子：总盈利 / 总亏损绝对值；无亏损返回 inf。"""
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    if losses <= 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def _win_rate(pnls: list[float]) -> float:
    if not pnls:
        return 0.0
    return sum(1 for p in pnls if p > 0) / len(pnls)


def kill_gate_check(
    trades: list[dict] | None = None,
    conn: sqlite3.Connection | None = None,
    gates: dict | None = None,
) -> dict:
    """击杀门禁检查。

    trades: 逐笔记录列表（含 pnl 字段，按 created_at 排序）；
            传 conn 时自动从 trade_records 取全部有 pnl 的记录。
    gates: 覆盖默认门槛（如 {"max_drawdown": 0.15}）。

    返回 {passed, killed_reasons, metrics, gates, n_trades}。
    passed=False 表示任一门槛未过（策略被击杀，禁止上线/继续）。
    """
    gates = {**DEFAULT_GATES, **(gates or {})}
    if trades is None and conn is not None:
        rows = conn.execute(
            "SELECT created_at, pnl FROM trade_records WHERE pnl IS NOT NULL"
        ).fetchall()
        trades = [dict(r) for r in rows]
    trades = trades or []

    pnls = _pnl_series(trades)
    n = len(pnls)

    metrics = {
        "n_trades": n,
        "max_drawdown": round(_max_drawdown(pnls), 4),
        "max_consecutive_losses": _max_consecutive_losses(pnls),
        "profit_factor": round(_profit_factor(pnls), 3) if pnls else None,
        "win_rate": round(_win_rate(pnls), 4) if pnls else None,
    }
    killed: list[str] = []

    # 样本不足直接击杀（数据不支撑结论，防过拟合）
    if n < gates["min_trades"]:
        killed.append(
            f"样本不足（{n} < {gates['min_trades']} 笔）：数据不支撑结论，禁止上线"
        )
        return {
            "passed": False, "killed_reasons": killed, "metrics": metrics,
            "gates": gates, "n_trades": n,
        }

    if metrics["max_drawdown"] > gates["max_drawdown"]:
        killed.append(
            f"最大回撤 {metrics['max_drawdown']:.1%} > 门槛 {gates['max_drawdown']:.0%}"
        )
    if metrics["max_consecutive_losses"] > gates["max_consecutive_losses"]:
        killed.append(
            f"最大连亏 {metrics['max_consecutive_losses']} 笔 > 门槛 {gates['max_consecutive_losses']} 笔"
        )
    if metrics["profit_factor"] is not None and metrics["profit_factor"] < gates["min_profit_factor"]:
        killed.append(
            f"盈利因子 {metrics['profit_factor']:.2f} < 门槛 {gates['min_profit_factor']}"
        )
    if metrics["win_rate"] is not None and metrics["win_rate"] < gates["min_win_rate"]:
        killed.append(
            f"胜率 {metrics['win_rate']:.1%} < 门槛 {gates['min_win_rate']:.0%}"
        )

    return {
        "passed": not killed,
        "killed_reasons": killed,
        "metrics": metrics,
        "gates": gates,
        "n_trades": n,
    }


def kill_gate_report(trades: list[dict] | None = None, conn: sqlite3.Connection | None = None) -> dict:
    """kill-gate 汇总：供复盘/推送引用。"""
    r = kill_gate_check(trades=trades, conn=conn)
    r["verdict"] = "通过（可上线）" if r["passed"] else "击杀（禁止上线/继续）"
    return r
