"""周度复盘：执行纪律检查（看过程不看盈亏）。"""
from __future__ import annotations

import datetime as dt
import sqlite3


def weekly_review(conn: sqlite3.Connection) -> dict:
    today = dt.date.today()
    last_monday = today - dt.timedelta(days=today.weekday())
    since = last_monday.isoformat()

    rogue = conn.execute(
        """SELECT COUNT(*) AS n FROM trade_records r
           JOIN trade_plans p ON r.plan_id = p.id
           WHERE p.status != 'active' AND date(r.created_at) >= ?""",
        (since,),
    ).fetchone()["n"]

    stop_hits = conn.execute(
        """SELECT COUNT(*) AS n FROM trade_records
           WHERE deviation_note LIKE '%止损%' AND date(created_at) >= ?""",
        (since,),
    ).fetchone()["n"]

    above = conn.execute(
        """SELECT COUNT(*) AS n FROM trade_records
           WHERE actual_vs_plan='above_range' AND date(created_at) >= ?""",
        (since,),
    ).fetchone()["n"]
    below = conn.execute(
        """SELECT COUNT(*) AS n FROM trade_records
           WHERE actual_vs_plan='below_range' AND date(created_at) >= ?""",
        (since,),
    ).fetchone()["n"]

    records_count = conn.execute(
        "SELECT COUNT(*) AS n FROM trade_records WHERE date(created_at) >= ?", (since,)
    ).fetchone()["n"]

    violations = []
    if rogue:
        violations.append(f"计划外交易 {rogue} 笔（计划非 active 仍有成交）")
    if above:
        violations.append(f"高于计划区间买入 {above} 笔")
    if below:
        violations.append(f"低于计划区间买入 {below} 笔")

    score = max(0, 100 - rogue * 30 - above * 10 - below * 5)
    return {
        "period": f"{since}~{today.isoformat()}",
        "trade_records": int(records_count),
        "rogue_trades": int(rogue),
        "stop_hits": int(stop_hits),
        "above_range": int(above),
        "below_range": int(below),
        "score": int(score),
        "violations": violations,
    }