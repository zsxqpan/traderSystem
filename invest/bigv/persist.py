"""观点入库（url 去重，短 view + 全文 body）。"""
from __future__ import annotations

import sqlite3

from invest.bigv.schema import ensure_bigv_schema

_VIEW_LEN = 60


def insert_opinion(
    conn: sqlite3.Connection,
    profile_id: str,
    *,
    url: str,
    title: str = "",
    text: str = "",
    opinion_date: str = "",
    symbol: str = "",
    bias: str = "",
) -> dict:
    ensure_bigv_schema(conn)
    url = (url or "").strip()
    if url:
        exist = conn.execute(
            "SELECT id FROM big_v_opinion WHERE url=? LIMIT 1", (url,)
        ).fetchone()
        if exist:
            return {"inserted": False, "id": exist["id"]}
    exist_p = conn.execute("SELECT 1 FROM big_v_profile WHERE id=?", (profile_id,)).fetchone()
    if not exist_p:
        return {"inserted": False, "error": f"画像不存在: {profile_id}"}
    body = (text or "").strip()
    topic = (title or "").strip()[:100]
    view = (topic or body or "（无摘要）")[:_VIEW_LEN]
    day = (opinion_date or "")[:10] or "1970-01-01"
    cur = conn.execute(
        """INSERT INTO big_v_opinion(
               profile_id, opinion_date, symbol, topic, view, bias, url, body)
           VALUES(?,?,?,?,?,?,?,?)""",
        (profile_id, day, symbol or "", topic, view, bias or "", url, body),
    )
    conn.commit()
    return {"inserted": True, "id": cur.lastrowid}
