"""观点准确率统计（v1：按已验证/已证伪状态计算）。"""
from __future__ import annotations

import sqlite3


def accuracy_stats(
    conn: sqlite3.Connection,
    group_by: str = "source",
) -> list[dict]:
    """按来源或周期统计：accuracy = verified / (verified + invalidated)。"""
    col = "source" if group_by == "source" else "period_tag"
    rows = conn.execute(
        f"""SELECT {col} AS g, status, COUNT(*) AS n
            FROM viewpoints
            WHERE status IN ('verified', 'invalidated')
            GROUP BY {col}, status"""
    ).fetchall()
    agg: dict = {}
    for r in rows:
        key = r["g"] or "unknown"
        agg.setdefault(key, {"verified": 0, "invalidated": 0})
        agg[key][r["status"]] = r["n"]
    out = []
    for key, counts in sorted(agg.items()):
        total = counts["verified"] + counts["invalidated"]
        out.append({
            "group": key,
            "verified": counts["verified"],
            "invalidated": counts["invalidated"],
            "accuracy": round(counts["verified"] / total, 4) if total else None,
        })
    return out