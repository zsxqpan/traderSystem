"""月度复盘：观点质量（命中率/在途/到期）。"""
from __future__ import annotations

import sqlite3

from invest.viewpoints.accuracy import accuracy_stats


def monthly_review(conn: sqlite3.Connection) -> dict:
    acc = accuracy_stats(conn, group_by="source")
    active = conn.execute("SELECT COUNT(*) AS n FROM viewpoints WHERE status='active'").fetchone()["n"]
    pending = conn.execute("SELECT COUNT(*) AS n FROM viewpoints WHERE status='pending_review'").fetchone()["n"]
    verified = conn.execute("SELECT COUNT(*) AS n FROM viewpoints WHERE status='verified'").fetchone()["n"]
    invalidated = conn.execute("SELECT COUNT(*) AS n FROM viewpoints WHERE status='invalidated'").fetchone()["n"]
    total = verified + invalidated
    return {
        "accuracy_by_source": acc,
        "active_viewpoints": int(active),
        "pending_review": int(pending),
        "verified": int(verified),
        "invalidated": int(invalidated),
        "overall_accuracy": round(verified / total, 4) if total else None,
    }