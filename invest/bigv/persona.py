"""从语料压人格卡；无 complete_fn 时用摘录，不调网上 LLM。"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime

from invest.bigv.schema import ensure_bigv_schema

logger = logging.getLogger(__name__)


def _split_persona_stamp(raw: str) -> tuple[str, int | None]:
    s = (raw or "").strip()
    if "#" in s:
        dt, _, sid = s.rpartition("#")
        try:
            return dt, int(sid)
        except ValueError:
            return s, None
    return s, None


def maybe_refresh_persona(
    conn: sqlite3.Connection,
    profile_id: str,
    *,
    complete_fn=None,
    now: str | None = None,
    max_age_days: int = 7,
    force: bool = False,
) -> dict:
    ensure_bigv_schema(conn)
    row = conn.execute("SELECT * FROM big_v_profile WHERE id=?", (profile_id,)).fetchone()
    if not row:
        return {"refreshed": False, "error": "画像不存在"}
    today = (now or date.today().isoformat())[:10]
    last_raw = (row["persona_updated_at"] or "").strip()
    last_dt, last_mid = _split_persona_stamp(last_raw)
    last = last_dt[:10]
    need = force or not (row["persona_card"] or "").strip() or not last
    if not need and last:
        try:
            need = (date.fromisoformat(today) - date.fromisoformat(last)).days >= max_age_days
        except ValueError:
            need = True
    if not need:
        cur_mid = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM big_v_opinion WHERE profile_id=?",
            (profile_id,),
        ).fetchone()[0]
        if last_mid is not None:
            need = cur_mid > last_mid
        elif last_raw:
            wm = last_raw if len(last_raw) > 10 else f"{last} 23:59:59"
            newer = conn.execute(
                """SELECT 1 FROM big_v_opinion
                   WHERE profile_id=?
                     AND datetime(ifnull(nullif(collected_at, ''), '1970-01-01'))
                         > datetime(?)
                   LIMIT 1""",
                (profile_id, wm),
            ).fetchone()
            need = bool(newer)
    if not need:
        return {"refreshed": False}

    texts = conn.execute(
        """SELECT opinion_date, topic, view, body
           FROM big_v_opinion WHERE profile_id=?
           ORDER BY opinion_date DESC LIMIT 20""",
        (profile_id,),
    ).fetchall()
    blob = "\n".join(
        f"{r['opinion_date']} {r['topic'] or ''}: {(r['body'] or r['view'] or '')[:400]}"
        for r in texts
    ) or "（暂无语料）"
    name = row["name"]
    system = (
        f"根据以下公开言论，为{name}写一张短人格卡（口吻、已知立场、常谈赛道、"
        "明确反对过什么、自相矛盾处、回答禁区）。只根据原文，不要编造。"
    )
    card = ""
    if complete_fn:
        try:
            card = (complete_fn(system, blob) or "").strip()
        except Exception as exc:
            logger.warning("人格卡 LLM 失败 %s: %s", profile_id, exc)
    if not card:
        card = blob[:800]
    if now and len(now.strip()) >= 19:
        stamp = now.strip()[:19]
    else:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if now and len(now.strip()) >= 10:
            stamp = now.strip()[:10] + stamp[10:]
    max_id = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM big_v_opinion WHERE profile_id=?",
        (profile_id,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE big_v_profile SET persona_card=?, persona_updated_at=? WHERE id=?",
        (card, f"{stamp}#{max_id}", profile_id),
    )
    conn.commit()
    return {"refreshed": True}
