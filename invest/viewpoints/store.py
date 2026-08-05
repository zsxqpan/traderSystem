"""观点库：CRUD + 生命周期状态机（更新产生新版本，保留历史）。"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from .schema import SOURCES, STATUSES, validate_viewpoint

TRANSITIONS = {
    "draft": {"active"},
    "active": {"verifying", "pending_review", "expired", "invalidated"},
    "verifying": {"verified", "invalidated"},
    "pending_review": {"active", "invalidated"},
    "updated": {"active", "expired", "invalidated"},  # 旧版本可复活/归档/证伪
}


def create_viewpoint(
    conn: sqlite3.Connection,
    *,
    source: str,
    conclusion: str,
    period_tag: str,
    confidence: float,
    evidence: list,
    invalid_condition: str,
    obj_type: str = "",
    obj: str = "",
    attention_level: str = "",
    valid_until: str | None = None,
    status: str = "active",
) -> int:
    if source not in SOURCES:
        raise ValueError(f"非法来源: {source}，允许 {SOURCES}")
    data = {
        "conclusion": conclusion, "period_tag": period_tag,
        "confidence": confidence, "evidence": evidence,
        "invalid_condition": invalid_condition,
    }
    validate_viewpoint(data)
    if status not in STATUSES:
        raise ValueError(f"非法状态: {status}")
    cur = conn.execute(
        """INSERT INTO viewpoints(source, obj_type, obj, attention_level, conclusion,
           period_tag, valid_until, confidence, evidence_json, invalid_condition, status, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'), datetime('now','localtime'))""",
        (source, obj_type, obj, attention_level, conclusion, period_tag,
         valid_until, confidence, json.dumps(evidence, ensure_ascii=False),
         invalid_condition, status),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_viewpoint(conn: sqlite3.Connection, vid: int) -> dict | None:
    row = conn.execute("SELECT * FROM viewpoints WHERE id=?", (vid,)).fetchone()
    return dict(row) if row else None


def list_viewpoints(
    conn: sqlite3.Connection,
    *,
    obj: str | None = None,
    status: str | None = None,
    source: str | None = None,
    period_tag: str | None = None,
    limit: int = 200,
) -> list[dict]:
    sql = "SELECT * FROM viewpoints WHERE 1=1"
    args: list = []
    if obj:
        sql += " AND obj=?"
        args.append(obj)
    if status:
        sql += " AND status=?"
        args.append(status)
    if source:
        sql += " AND source=?"
        args.append(source)
    if period_tag:
        sql += " AND period_tag=?"
        args.append(period_tag)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def update_viewpoint(
    conn: sqlite3.Connection,
    vid: int,
    *,
    conclusion: str | None = None,
    period_tag: str | None = None,
    confidence: float | None = None,
    evidence: list | None = None,
    invalid_condition: str | None = None,
    valid_until: str | None = None,
    review_note: str = "",
) -> int:
    """更新产生新版本：旧观点标记 updated，新观点 active。"""
    old = get_viewpoint(conn, vid)
    if old is None:
        raise ValueError(f"观点 {vid} 不存在")
    merged = {
        "conclusion": conclusion if conclusion is not None else old["conclusion"],
        "period_tag": period_tag if period_tag is not None else old["period_tag"],
        "confidence": confidence if confidence is not None else old["confidence"],
        "evidence": evidence if evidence is not None else json.loads(old["evidence_json"] or "[]"),
        "invalid_condition": invalid_condition if invalid_condition is not None else old["invalid_condition"],
    }
    validate_viewpoint(merged)
    conn.execute(
        "UPDATE viewpoints SET status='updated', review_note=?, updated_at=datetime('now','localtime') WHERE id=?",
        (review_note, vid),
    )
    new_id = create_viewpoint(
        conn,
        source=old["source"], obj_type=old["obj_type"], obj=old["obj"],
        attention_level=old["attention_level"],
        valid_until=valid_until if valid_until is not None else old["valid_until"],
        **merged,
    )
    return new_id


def transition(conn: sqlite3.Connection, vid: int, new_status: str, note: str = "") -> None:
    old = get_viewpoint(conn, vid)
    if old is None:
        raise ValueError(f"观点 {vid} 不存在")
    allowed = TRANSITIONS.get(old["status"], set())
    if new_status not in allowed:
        raise ValueError(f"不允许的状态迁移: {old['status']} -> {new_status}")
    conn.execute(
        "UPDATE viewpoints SET status=?, review_note=?, updated_at=datetime('now','localtime') WHERE id=?",
        (new_status, note, vid),
    )
    conn.commit()


def expire_due(conn: sqlite3.Connection) -> int:
    """到期观点进入复盘队列（pending_review）。返回处理数量。"""
    today = dt.date.today().isoformat()
    cur = conn.execute(
        """UPDATE viewpoints SET status='pending_review',
           review_note=COALESCE(review_note, '') || ' | 到期待复盘',
           updated_at=datetime('now','localtime')
           WHERE status IN ('active','verifying') AND valid_until IS NOT NULL AND valid_until < ?""",
        (today,),
    )
    conn.commit()
    return cur.rowcount