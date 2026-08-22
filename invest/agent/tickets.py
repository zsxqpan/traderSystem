"""工单机制：方向提示单 / 归因请求单。"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

TICKET_TYPES = ("direction_hint", "attribution_request")
STATUSES = ("created", "accepted", "in_progress", "resolved", "rejected", "expired")


def create_ticket(
    conn: sqlite3.Connection,
    type_: str,
    from_agent: str,
    to_agent: str,
    direction: str = "",
    payload: dict | None = None,
    deadline: str | None = None,
) -> int:
    if type_ not in TICKET_TYPES:
        raise ValueError(f"工单类型必须为 {TICKET_TYPES}")
    cur = conn.execute(
        """INSERT INTO tickets(type, direction, from_agent, to_agent, payload_json, status, deadline, created_at)
           VALUES(?,?,?,?,?, 'created', ?, datetime('now','localtime'))""",
        (type_, direction, from_agent, to_agent, json.dumps(payload or {}, ensure_ascii=False), deadline),
    )
    conn.commit()
    lastrowid = cur.lastrowid
    return int(lastrowid) if lastrowid is not None else 0


def list_tickets(
    conn: sqlite3.Connection,
    status: str | None = None,
    type_: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM tickets WHERE 1=1"
    args: list = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if type_:
        sql += " AND type=?"
        args.append(type_)
    sql += " ORDER BY created_at"
    return [dict(r) for r in conn.execute(sql, args)]


def update_status(conn: sqlite3.Connection, ticket_id: int, status: str, note: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(f"工单状态必须为 {STATUSES}")
    conn.execute(
        """UPDATE tickets SET status=?, resolved_at=datetime('now','localtime') WHERE id=?""",
        (status, ticket_id),
    )
    conn.commit()


def expire_overdue(conn: sqlite3.Connection) -> int:
    """超时未处理的工单置为 expired。"""
    now = dt.datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """UPDATE tickets SET status='expired', resolved_at=datetime('now','localtime')
           WHERE status IN ('created','accepted','in_progress') AND deadline IS NOT NULL AND deadline < ?""",
        (now,),
    )
    conn.commit()
    return cur.rowcount