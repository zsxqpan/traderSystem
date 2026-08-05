"""执行留痕：交易记录、计划偏差、止损触发标记。"""
from __future__ import annotations

import sqlite3


def record_trade(
    conn: sqlite3.Connection,
    plan_id: int,
    action: str,
    price: float,
    qty: int,
    emotion_note: str = "",
) -> dict:
    """记录一笔成交；与计划对照生成偏差标注。"""
    if action not in ("buy", "sell"):
        raise ValueError("action 必须为 buy/sell")
    plan = conn.execute("SELECT * FROM trade_plans WHERE id=?", (plan_id,)).fetchone()
    if plan is None:
        raise ValueError(f"计划 {plan_id} 不存在")
    plan = dict(plan)
    actual_vs_plan, deviation_note = _deviation(plan, action, price)
    cur = conn.execute(
        """INSERT INTO trade_records(plan_id, action, price, qty, actual_vs_plan, deviation_note, emotion_note, created_at)
           VALUES(?,?,?,?,?,?,?, datetime('now','localtime'))""",
        (plan_id, action, price, qty, actual_vs_plan, deviation_note, emotion_note),
    )
    conn.commit()
    return {"record_id": cur.lastrowid, "actual_vs_plan": actual_vs_plan, "deviation_note": deviation_note}


def _deviation(plan: dict, action: str, price: float) -> tuple[str, str]:
    notes = []
    if action == "buy" and plan.get("buy_range"):
        parts = str(plan["buy_range"]).replace("，", ",").split(",")
        try:
            low, high = float(parts[0]), float(parts[1])
            if price < low:
                actual_vs_plan = "below_range"
                notes.append(f"成交价 {price} 低于计划区间 {low}-{high}")
            elif price > high:
                actual_vs_plan = "above_range"
                notes.append(f"成交价 {price} 高于计划区间 {low}-{high}")
            else:
                actual_vs_plan = "in_range"
        except (ValueError, IndexError):
            actual_vs_plan = "unknown"
    else:
        actual_vs_plan = "unknown"
    if plan.get("stop_loss") is not None and price <= float(plan["stop_loss"]):
        notes.append(f"触发止损位 {plan['stop_loss']}")
    return actual_vs_plan, "; ".join(notes)


def list_records(conn: sqlite3.Connection, plan_id: int | None = None) -> list[dict]:
    if plan_id is None:
        rows = conn.execute("SELECT * FROM trade_records ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trade_records WHERE plan_id=? ORDER BY created_at", (plan_id,)
        ).fetchall()
    return [dict(r) for r in rows]