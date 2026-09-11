"""D33 动作清单（只读 daily_actions）。"""
from __future__ import annotations

SKILL = {
    "id": "d33_daily_actions",
    "name": "动作清单",
    "kind": "section",
    "description": "当日动作清单（买/卖/等/减；date 默认库内最新）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "date": "str, optional, YYYY-MM-DD，空=最新有数据日",
    },
}


def render(db_path: str, date: str = "") -> str:
    from invest.actions.format import format_actions
    from invest.actions.query import list_actions
    from invest.actions.types import Action
    from invest.db import connect

    conn = connect(db_path)
    try:
        day = (date or "").strip() or None
        rows = list_actions(conn, day)
    except Exception:
        return ""
    finally:
        conn.close()
    acts = [
        Action(
            date=r.get("date") or "", symbol=r.get("symbol") or "",
            verb=r.get("verb") or "hold", priority=int(r.get("priority") or 2),
            source=r.get("source") or "", hint=r.get("hint") or "",
            entry_lo=r.get("entry_lo"), entry_hi=r.get("entry_hi"),
            stop_loss=r.get("stop_loss"), target=r.get("target"),
            status=r.get("status") or "pending",
        )
        for r in rows
    ]
    return format_actions(acts, title="【动作清单】")
