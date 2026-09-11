"""daily_actions 只读查询。缺表/缺列返回 []。"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date

logger = logging.getLogger(__name__)

_NEED = {"date", "symbol", "verb", "priority", "status"}


def _iso_day(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    s = str(value).strip()
    return s[:10] if s else None


def list_actions(
    conn: sqlite3.Connection,
    asof: date | str | None = None,
    *,
    priorities: list[int] | None = None,
    verbs: list[str] | None = None,
    status: str | None = None,
    limit: int = 80,
) -> list[dict]:
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_actions)")}
        if not cols or not _NEED.issubset(cols):
            logger.warning("daily_actions 不存在或缺列，请跑 init_db（SCHEMA 15）")
            return []
        day = _iso_day(asof)
        if day is None:
            row = conn.execute("SELECT MAX(date) AS d FROM daily_actions").fetchone()
            day = row["d"] if row else None
        if not day:
            return []
        sql = (
            "SELECT date, symbol, name, verb, priority, source, source_ref, "
            "entry_lo, entry_hi, stop_loss, target, invalid_condition, "
            "position_hint, status, hint, evidence "
            "FROM daily_actions WHERE date=?"
        )
        params: list = [day]
        if priorities:
            sql += " AND priority IN (%s)" % ",".join("?" * len(priorities))
            params.extend(int(p) for p in priorities)
        if verbs:
            sql += " AND verb IN (%s)" % ",".join("?" * len(verbs))
            params.extend(verbs)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY priority, symbol LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(sql, params)]
    except Exception as exc:
        logger.warning("list_actions 失败: %s", exc)
        return []
