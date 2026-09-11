"""大V 名单：解析雪球 ID、登记、停跟。"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3

from invest.bigv.schema import ensure_bigv_schema

_XQ_RE = re.compile(r"xueqiu\.com/(?:u/)?(\d+)", re.IGNORECASE)
_KEEP_ON_REPLACE = (
    "watched", "persona_card", "persona_updated_at",
    "last_harvest_at", "last_harvest_new", "last_harvest_error",
    "backfill_cursor", "backfill_done",
)


def preserve_profile_fields(conn: sqlite3.Connection, row: dict) -> dict:
    """upsert_df 整行替换前，把采集/人格卡字段从旧行拷回。"""
    pid = row.get("id")
    if not pid:
        return row
    old = conn.execute("SELECT * FROM big_v_profile WHERE id=?", (pid,)).fetchone()
    if not old:
        return row
    out = dict(row)
    for col in _KEEP_ON_REPLACE:
        out[col] = old[col]
    return out


def parse_xueqiu_id(raw: str) -> str | None:
    s = (raw or "").strip()
    if s.isdigit():
        return s
    m = _XQ_RE.search(s)
    return m.group(1) if m else None


def register(conn: sqlite3.Connection, name: str = "", xueqiu: str = "") -> dict:
    """登记或更新一人。返回 {ok, profile_id?, error?}。"""
    ensure_bigv_schema(conn)
    nm = (name or "").strip()
    uid = parse_xueqiu_id(xueqiu)
    if not nm:
        return {"ok": False, "error": "需要显示名"}
    if not uid:
        return {"ok": False, "error": "需要雪球数字 ID 或主页 URL"}
    pid = f"xq_{uid}"
    homepage = f"https://xueqiu.com/u/{uid}"
    today = dt.date.today().isoformat()
    exist = conn.execute("SELECT id FROM big_v_profile WHERE id=?", (pid,)).fetchone()
    if exist:
        conn.execute(
            """UPDATE big_v_profile
               SET name=?, xueqiu_id=?, homepage=?, platform='xueqiu',
                   watched=1, updated_at=?
               WHERE id=?""",
            (nm, uid, homepage, today, pid),
        )
    else:
        conn.execute(
            """INSERT INTO big_v_profile(
                   id, name, platform, xueqiu_id, homepage, watched, updated_at,
                   backfill_done)
               VALUES(?,?,?,?,?,1,?,0)""",
            (pid, nm, "xueqiu", uid, homepage, today),
        )
    conn.commit()
    return {"ok": True, "profile_id": pid}


def set_watched(conn: sqlite3.Connection, profile_id: str, watched: int) -> dict:
    ensure_bigv_schema(conn)
    cur = conn.execute(
        "UPDATE big_v_profile SET watched=? WHERE id=?",
        (1 if watched else 0, profile_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": "画像不存在"}
    return {"ok": True, "profile_id": profile_id}


def list_watched(conn: sqlite3.Connection) -> list[dict]:
    ensure_bigv_schema(conn)
    rows = conn.execute(
        """SELECT id, name, xueqiu_id, homepage, last_harvest_at, last_harvest_new,
                  last_harvest_error, backfill_done, persona_updated_at
           FROM big_v_profile
           WHERE watched=1
           ORDER BY name COLLATE NOCASE"""
    ).fetchall()
    return [dict(r) for r in rows]


def resolve_name(conn: sqlite3.Connection, name: str = "", profile_id: str = "") -> dict:
    """精确 id 或按姓名模糊。多命中视为重名。"""
    ensure_bigv_schema(conn)
    if profile_id:
        row = conn.execute("SELECT * FROM big_v_profile WHERE id=?", (profile_id,)).fetchone()
        if not row:
            return {"status": "missing", "profiles": []}
        return {"status": "ok", "profile": dict(row), "profiles": [dict(row)]}
    nm = (name or "").strip()
    if not nm:
        return {"status": "missing", "profiles": []}
    exact = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM big_v_profile WHERE watched=1 AND name=? ORDER BY id",
            (nm,),
        ).fetchall()
    ]
    if len(exact) == 1:
        return {"status": "ok", "profile": exact[0], "profiles": exact}
    if len(exact) > 1:
        return {"status": "ambiguous", "profiles": exact}
    likes = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM big_v_profile
               WHERE watched=1 AND name LIKE ? AND name != ?
               ORDER BY name""",
            (f"%{nm}%", nm),
        ).fetchall()
    ]
    if likes:
        return {"status": "ambiguous", "profiles": likes}
    return {"status": "missing", "profiles": []}
