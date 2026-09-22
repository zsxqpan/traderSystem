"""daily_actions / review_lessons 落库。"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date

from invest.actions.types import Action

logger = logging.getLogger(__name__)


def _iso_day(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def persist_actions(
    conn: sqlite3.Connection,
    actions: list[Action],
    asof: date | str,
) -> None:
    day = _iso_day(asof)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_actions)")}
    if not cols:
        logger.warning("daily_actions 不存在，跳过 persist")
        return
    conn.execute("DELETE FROM daily_actions WHERE date=?", (day,))
    for a in actions:
        conn.execute(
            """INSERT OR REPLACE INTO daily_actions
               (date, symbol, name, verb, priority, source, source_ref,
                entry_lo, entry_hi, stop_loss, target, invalid_condition,
                position_hint, status, hint, evidence, src, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'actions',
                       datetime('now','localtime'))""",
            (
                day, a.symbol, a.name or "", a.verb, int(a.priority),
                a.source, a.source_ref or "",
                a.entry_lo, a.entry_hi, a.stop_loss, a.target,
                a.invalid_condition or "", a.position_hint or "",
                a.status or "pending", a.hint or "",
                json.dumps(a.evidence or {}, ensure_ascii=False),
            ),
        )
    conn.commit()


def update_statuses(
    conn: sqlite3.Connection,
    asof: date | str,
    quotes: dict[str, float],
) -> int:
    """只改 status / evidence 现价，不改 verb 与价位。返回更新行数。"""
    from invest.actions.query import list_actions

    day = _iso_day(asof)
    rows = list_actions(conn, day)
    n = 0
    for r in rows:
        sym = r["symbol"]
        price = quotes.get(sym)
        if price is None:
            continue
        status = r.get("status") or "pending"
        if status in ("done", "skipped"):
            continue
        lo, hi = r.get("entry_lo"), r.get("entry_hi")
        sl = r.get("stop_loss")
        hit = False
        try:
            px = float(price)
            if px <= 0:
                continue
            if lo is not None and hi is not None and float(lo) <= px <= float(hi):
                hit = True
            if sl is not None and float(sl) > 0 and px <= float(sl):
                hit = True
        except (TypeError, ValueError):
            continue
        new_status = "triggered" if hit else "pending"
        ev = r.get("evidence")
        if isinstance(ev, str):
            try:
                ev = json.loads(ev) if ev else {}
            except ValueError:
                ev = {}
        if not isinstance(ev, dict):
            ev = {}
        ev["last"] = px
        conn.execute(
            """UPDATE daily_actions SET status=?, evidence=?,
               updated_at=datetime('now','localtime')
               WHERE date=? AND symbol=?""",
            (new_status, json.dumps(ev, ensure_ascii=False), day, sym),
        )
        n += 1
    if n:
        conn.commit()
    return n


def mark_action(
    conn: sqlite3.Connection,
    asof: date | str,
    symbol: str,
    status: str,
) -> None:
    if status not in ("done", "skipped"):
        raise ValueError("status 只能是 done/skipped")
    day = _iso_day(asof)
    cur = conn.execute(
        """UPDATE daily_actions SET status=?, updated_at=datetime('now','localtime')
           WHERE date=? AND symbol=?""",
        (status, day, symbol),
    )
    if cur.rowcount == 0:
        raise ValueError(f"{day} {symbol} 无动作行")
    conn.commit()


def expire_active_plans(conn: sqlite3.Connection) -> None:
    """把 source=plan 且 obj=plan 的旧 active 行标 expired。不动研究观点。"""
    try:
        conn.execute(
            """UPDATE viewpoints SET status='expired',
               updated_at=datetime('now','localtime')
               WHERE source='plan' AND IFNULL(obj,'plan')='plan' AND status='active'"""
        )
    except Exception as exc:
        logger.warning("expire_active_plans 失败: %s", exc)


# 2026-09-18：删除 review_lessons「校验库」（persist_lessons / list_lessons）——
# 盘后复盘不再产出错误原因/经验，相关沉淀链路与建表一并移除。

