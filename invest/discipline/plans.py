"""交易计划：无计划不交易；标的必须来自候选池；必须设止损。"""
from __future__ import annotations

import sqlite3

from .pool import list_pool
from .rating import get_position_limit


def create_plan(
    conn: sqlite3.Connection,
    symbol: str,
    ref_viewpoint_id: int | None = None,
    buy_range: str = "",
    target_position: float | None = None,
    stop_loss: float | None = None,
    take_profit: str = "",
    invalid_condition: str = "",
) -> dict:
    pool = list_pool(conn, active_only=True)
    if symbol not in {p["symbol"] for p in pool}:
        raise ValueError(f"{symbol} 不在候选池中，禁止建计划")
    if stop_loss is None:
        raise ValueError("交易计划必须设置止损位")
    if target_position is not None:
        total_cap = get_position_limit(conn)
        if target_position > total_cap:
            raise ValueError(f"目标仓位 {target_position:.0%} 超过当前评级总仓位上限 {total_cap:.0%}")
    cur = conn.execute(
        """INSERT INTO trade_plans(symbol, ref_viewpoint_id, buy_range, target_position, stop_loss,
                                  take_profit, invalid_condition, status, created_at)
           VALUES(?,?,?,?,?,?,?, 'active', datetime('now','localtime'))""",
        (symbol, ref_viewpoint_id, buy_range, target_position, stop_loss, take_profit, invalid_condition),
    )
    conn.commit()
    return {"plan_id": cur.lastrowid, "symbol": symbol, "status": "active"}


def list_active_plans(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trade_plans WHERE status='active' ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def close_plan(conn: sqlite3.Connection, plan_id: int, status: str = "closed") -> None:
    if status not in ("closed", "cancelled"):
        raise ValueError("status 必须为 closed/cancelled")
    conn.execute("UPDATE trade_plans SET status=? WHERE id=?", (status, plan_id))
    conn.commit()