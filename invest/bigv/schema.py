"""大V 画像库表增量与 FTS（幂等，可供 init_db / 运行时调用）。"""
from __future__ import annotations

import sqlite3

PROFILE_ADDS = (
    ("watched", "INTEGER NOT NULL DEFAULT 0"),
    ("persona_card", "TEXT"),
    ("persona_updated_at", "TEXT"),
    ("last_harvest_at", "TEXT"),
    ("last_harvest_new", "INTEGER"),
    ("last_harvest_error", "TEXT"),
    ("backfill_cursor", "TEXT"),
    ("backfill_done", "INTEGER NOT NULL DEFAULT 0"),
)

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS big_v_opinion_fts USING fts5(
    profile_id UNINDEXED,
    topic,
    view,
    body,
    tokenize = 'unicode61'
);
CREATE TRIGGER IF NOT EXISTS trg_big_v_opinion_ai AFTER INSERT ON big_v_opinion BEGIN
    INSERT INTO big_v_opinion_fts(rowid, profile_id, topic, view, body)
    VALUES (new.id, new.profile_id, coalesce(new.topic,''), coalesce(new.view,''),
            coalesce(new.body,''));
END;
CREATE TRIGGER IF NOT EXISTS trg_big_v_opinion_ad AFTER DELETE ON big_v_opinion BEGIN
    DELETE FROM big_v_opinion_fts WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_big_v_opinion_au AFTER UPDATE ON big_v_opinion BEGIN
    DELETE FROM big_v_opinion_fts WHERE rowid = old.id;
    INSERT INTO big_v_opinion_fts(rowid, profile_id, topic, view, body)
    VALUES (new.id, new.profile_id, coalesce(new.topic,''), coalesce(new.view,''),
            coalesce(new.body,''));
END;
"""


def _col_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def ensure_bigv_schema(conn: sqlite3.Connection) -> None:
    """给旧库补列、建 FTS 与触发器，并回填未索引的观点。"""
    pcols = _col_names(conn, "big_v_profile")
    if pcols:
        for name, decl in PROFILE_ADDS:
            if name not in pcols:
                conn.execute(f"ALTER TABLE big_v_profile ADD COLUMN {name} {decl}")
    ocols = _col_names(conn, "big_v_opinion")
    if ocols and "body" not in ocols:
        conn.execute("ALTER TABLE big_v_opinion ADD COLUMN body TEXT")
    conn.executescript(FTS_SQL)
    if not ocols:
        return
    n_o = conn.execute("SELECT COUNT(*) FROM big_v_opinion").fetchone()[0]
    try:
        n_f = conn.execute("SELECT COUNT(*) FROM big_v_opinion_fts").fetchone()[0]
    except sqlite3.Error:
        n_f = -1
    if n_f != n_o:
        conn.execute("DELETE FROM big_v_opinion_fts")
        conn.execute(
            """INSERT INTO big_v_opinion_fts(rowid, profile_id, topic, view, body)
               SELECT id, profile_id, coalesce(topic,''), coalesce(view,''), coalesce(body,'')
               FROM big_v_opinion"""
        )
