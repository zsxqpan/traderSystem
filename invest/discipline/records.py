"""执行留痕：交易记录、计划偏差、周期漂移检测（[A]6，v2 6.3）。"""
from __future__ import annotations

import datetime as dt
import sqlite3

# 周期最大持有天数（v2 6.3 周期漂移检测阈值；超期未平仓即漂移）
CYCLE_MAX_DAYS = {
    "micro": 10,     # 超短：≤10 交易日
    "short": 25,     # 短线：≤25 交易日
    "mid": 65,       # 中线：≤65 交易日
    "long": 130,     # 长线：≤130 交易日
    "波段": 40,
    "配置": 120,
}


def cycle_max_days(cycle: str) -> int:
    """周期 → 最大持有天数（未知周期给短线默认 25）。"""
    return CYCLE_MAX_DAYS.get(cycle, CYCLE_MAX_DAYS["short"])


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


# ---------- 周期漂移检测（[A]6，v2 6.3） ----------

def _plan_cycle(conn: sqlite3.Connection, plan: dict) -> str:
    """计划周期：优先卡片 cycle（同 symbol 最新一张），否则按计划年龄推断。"""
    row = conn.execute(
        """SELECT cycle FROM cards WHERE symbol=?
           AND status IN ('locked','review','candidate') ORDER BY id DESC LIMIT 1""",
        (plan.get("symbol", ""),),
    ).fetchone()
    if row and row["cycle"]:
        return str(row["cycle"])
    return ""


def detect_cycle_drift(
    conn: sqlite3.Connection,
    as_of: str | None = None,
) -> list[dict]:
    """周期漂移检测：active 计划持有的自然日数超过其周期上限 → 漂移。

    返回 [{plan_id, symbol, cycle, max_days, held_days, drift_days, note}]。
    无周期标注的计划跳过（不臆断漂移）。
    """
    as_of = as_of or dt.date.today().isoformat()
    plans = conn.execute(
        "SELECT * FROM trade_plans WHERE status='active' ORDER BY created_at"
    ).fetchall()
    out: list[dict] = []
    for p in plans:
        p = dict(p)
        cycle = _plan_cycle(conn, p)
        if not cycle:
            continue
        max_days = cycle_max_days(cycle)
        try:
            created = dt.datetime.strptime(p["created_at"][:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        held = (dt.date.fromisoformat(as_of) - created).days
        if held > max_days:
            out.append({
                "plan_id": p["id"],
                "symbol": p["symbol"],
                "cycle": cycle,
                "max_days": max_days,
                "held_days": held,
                "drift_days": held - max_days,
                "note": f"{cycle}周期计划持有 {held} 天超过上限 {max_days} 天（漂移 {held - max_days} 天），应复核是否仍属计划内",
            })
    return out


def drift_report(conn: sqlite3.Connection, as_of: str | None = None) -> dict:
    """周期漂移汇总：n_drift + 明细；供周度复盘/推送引用。"""
    drifts = detect_cycle_drift(conn, as_of=as_of)
    return {
        "n_drift": len(drifts),
        "drifts": drifts,
        "violations": [d["note"] for d in drifts],
    }