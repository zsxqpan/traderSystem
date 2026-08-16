"""月度复盘：观点质量（命中率/在途/到期）+ 环境质量检查（[A]7）。"""
from __future__ import annotations

import datetime as dt
import sqlite3

from invest.viewpoints.accuracy import accuracy_stats


def monthly_review(conn: sqlite3.Connection) -> dict:
    acc = accuracy_stats(conn, group_by="source")
    active = conn.execute("SELECT COUNT(*) AS n FROM viewpoints WHERE status='active'").fetchone()["n"]
    pending = conn.execute("SELECT COUNT(*) AS n FROM viewpoints WHERE status='pending_review'").fetchone()["n"]
    verified = conn.execute("SELECT COUNT(*) AS n FROM viewpoints WHERE status='verified'").fetchone()["n"]
    invalidated = conn.execute("SELECT COUNT(*) AS n FROM viewpoints WHERE status='invalidated'").fetchone()["n"]
    total = verified + invalidated
    env = environment_quality(conn)
    return {
        "accuracy_by_source": acc,
        "active_viewpoints": int(active),
        "pending_review": int(pending),
        "verified": int(verified),
        "invalidated": int(invalidated),
        "overall_accuracy": round(verified / total, 4) if total else None,
        "environment_quality": env,
    }


def environment_quality(conn: sqlite3.Connection, months: int = 3) -> dict:
    """月度环境质量检查（[A]7）：宏观/市场评级是否频繁跳变、数据新鲜度是否达标。

    输出：
    - rating_changes: 近 months 月评级变化次数（宏观/市场分别统计）；
    - unstable: 评级月内变化 >= 3 次视为不稳定（提示评级体系可能过度反应）；
    - data_quality: pit.quality_report 非 valid 的表清单；
    - verdict: ok / warn（不稳定或数据异常时 warn）。
    """
    cutoff = (dt.date.today().replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    cutoff = cutoff - dt.timedelta(days=(months - 1) * 31)
    since = cutoff.isoformat()

    changes: dict[str, int] = {}
    for kind in ("macro", "market"):
        rows = conn.execute(
            """SELECT value FROM ratings WHERE kind=? AND date >= ? ORDER BY date""",
            (kind, since),
        ).fetchall()
        n = sum(1 for a, b in zip(rows, rows[1:]) if a["value"] != b["value"])
        changes[kind] = n

    unstable = [k for k, n in changes.items() if n >= 3]
    data_quality: dict[str, str] = {}
    try:
        from invest.data.pit import quality_report
        report = quality_report(conn)
        data_quality = {t: st for t, (st, _info) in report.items() if st != "valid"}
    except Exception:  # noqa: BLE001
        pass

    warn = []
    if unstable:
        warn.append(f"评级不稳定: {', '.join(unstable)} 近{months}月变化 >= 3 次")
    if data_quality:
        warn.append("数据质量异常: " + ", ".join(f"{t}={st}" for t, st in list(data_quality.items())[:8]))
    return {
        "months": months,
        "rating_changes": changes,
        "unstable": unstable,
        "data_quality": data_quality,
        "verdict": "warn" if warn else "ok",
        "warnings": warn,
    }
