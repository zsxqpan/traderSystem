"""归因体系（v3 15.3，2026-08-15）：周期×等级×主价差类型×市场状态×入场分位。

从 trade_records 按维度切片统计盈亏，识别收益来源与亏损集中处。
- attribution_report(): 五维归因汇总；
- breakdown_by(): 单维度切片（n/mean/win_rate/total_pnl）；
- top_losers(): 亏损集中度（贡献最大的亏损标的）。
"""
from __future__ import annotations

import sqlite3

DIMENSIONS = ("cycle", "level", "spread_type", "market_state", "entry_pct")


def _records(conn: sqlite3.Connection) -> list[dict]:
    """取 trade_records 关联 plan/pool/ratings 的宽表（含各维度标签）。

    v1 简化：trade_records 无维度列，用 trade_plans.symbol 关联 candidate_pool.level
    近似 level；其余维度（cycle/spread_type/market_state/entry_pct）由调用方
    通过 rec 扩展字段传入（见 build_attributed_records）。
    """
    rows = conn.execute(
        """SELECT tr.*, tp.symbol, cp.level AS level
           FROM trade_records tr
           JOIN trade_plans tp ON tr.plan_id = tp.id
           LEFT JOIN candidate_pool cp ON tp.symbol = cp.symbol
           WHERE tr.pnl IS NOT NULL
           ORDER BY tr.created_at"""
    ).fetchall()
    return [dict(r) for r in rows]


def build_attributed_records(
    conn: sqlite3.Connection,
    cycle: str = "short",
    spread_type: str = "波段价差",
    market_state: str = "中性",
    entry_pct: str = "40-60%",
) -> list[dict]:
    """构造带五维标签的交易记录（v1 固定维度值，后续可接实际数据源）。"""
    recs = _records(conn)
    for r in recs:
        r["cycle"] = cycle
        r["spread_type"] = spread_type
        r["market_state"] = market_state
        r["entry_pct"] = entry_pct
    return recs


def breakdown_by(recs: list[dict], dim: str) -> list[dict]:
    """单维度切片统计：按 dim 值分组，输出 n/mean/win_rate/total_pnl。"""
    groups: dict[str, list[float]] = {}
    for r in recs:
        key = r.get(dim) or "未知"
        groups.setdefault(key, []).append(float(r["pnl"]))
    out = []
    for key, pnls in groups.items():
        out.append({
            "dim": dim,
            "value": key,
            "n": len(pnls),
            "mean_pnl": round(sum(pnls) / len(pnls), 2),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
            "total_pnl": round(sum(pnls), 2),
        })
    out.sort(key=lambda x: -x["total_pnl"])
    return out


def attribution_report(conn: sqlite3.Connection, recs: list[dict] | None = None) -> dict:
    """五维归因汇总报告。"""
    recs = recs if recs is not None else _records(conn)
    if not recs:
        return {"ok": False, "note": "无带 pnl 的交易记录", "dimensions": {}}
    dims = {}
    for dim in DIMENSIONS:
        dims[dim] = breakdown_by(recs, dim)
    total = sum(float(r["pnl"]) for r in recs)
    wins = sum(1 for r in recs if float(r["pnl"]) > 0)
    return {
        "ok": True,
        "n": len(recs),
        "wins": wins,
        "total_pnl": round(total, 2),
        "win_rate": round(wins / len(recs), 4),
        "dimensions": dims,
    }


def top_losers(recs: list[dict], top: int = 5) -> list[dict]:
    """亏损集中度：亏损贡献最大的标的（按 pnl 升序取前 top）。"""
    by_symbol: dict[str, float] = {}
    for r in recs:
        sym = r.get("symbol") or "?"
        by_symbol[sym] = by_symbol.get(sym, 0.0) + float(r["pnl"])
    losers = sorted(by_symbol.items(), key=lambda x: x[1])[:top]
    return [{"symbol": s, "total_pnl": round(p, 2)} for s, p in losers if p < 0]
