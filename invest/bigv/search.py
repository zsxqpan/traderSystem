"""对该人语料做 FTS 检索。"""
from __future__ import annotations

import re
import sqlite3

from invest.bigv.schema import ensure_bigv_schema

_CJK = re.compile(r"[\u4e00-\u9fff]{2,}")
_WORD = re.compile(r"[A-Za-z0-9]{2,}")
_FTS_OPS = {"AND", "OR", "NOT", "NEAR"}
# 问句填充词：不能拿来当有据命中（「怎么走」误中「怎么看比特币」）
_STOP = (
    "有没有", "是不是", "为什么", "怎么样", "怎么办", "怎么看", "怎么加",
    "怎么", "怎样", "如何", "什么", "一下", "这个", "那个", "我们", "你们",
    "他们", "一个", "可以", "还是", "还要", "不要", "为何", "是否", "看看",
    "谈谈", "说说", "观点", "看法", "咋看", "请问", "帮我", "分析", "解读",
)
_STOP_SET = set(_STOP)
_STOP_PHRASES = tuple(sorted(_STOP, key=len, reverse=True))


def extract_terms(question: str) -> list[str]:
    """抽出可用于检索的词；丢掉问句套话。"""
    q = (question or "").strip()
    terms: list[str] = []
    for m in _WORD.finditer(q):
        w = m.group(0)
        if w.upper() in _FTS_OPS:
            continue
        terms.append(w)
    for m in _CJK.finditer(q):
        parts = [m.group(0)]
        for stop in _STOP_PHRASES:
            nxt: list[str] = []
            for part in parts:
                nxt.extend(part.split(stop))
            parts = nxt
        for part in parts:
            part = part.strip()
            if len(part) >= 2 and part not in _STOP_SET:
                terms.append(part)
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= 12:
            break
    return out


def fts_match_query(question: str) -> str:
    bits = []
    for t in extract_terms(question):
        t = t.replace('"', "").replace("'", "")
        if t:
            bits.append(f'"{t}"')
    return " OR ".join(bits)


def search_opinions(
    conn: sqlite3.Connection,
    profile_id: str,
    question: str,
    limit: int = 8,
) -> list[dict]:
    ensure_bigv_schema(conn)
    limit = max(1, min(int(limit or 8), 20))
    match = fts_match_query(question)
    hits: list[dict] = []
    if match:
        try:
            rows = conn.execute(
                """SELECT o.id, o.opinion_date, o.topic, o.view, o.body, o.url
                   FROM big_v_opinion_fts f
                   JOIN big_v_opinion o ON o.id = f.rowid
                   WHERE f.profile_id = ? AND big_v_opinion_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (profile_id, match, limit),
            ).fetchall()
            hits = [dict(r) for r in rows]
        except sqlite3.Error:
            hits = []
    if hits:
        return hits
    terms = extract_terms(question)
    if not terms:
        return []
    clauses = []
    params: list = [profile_id]
    for t in terms:
        clauses.append(
            "(ifnull(body,'') LIKE ? OR ifnull(topic,'') LIKE ? OR ifnull(view,'') LIKE ?)"
        )
        like = f"%{t}%"
        params.extend((like, like, like))
    params.append(limit)
    rows = conn.execute(
        f"""SELECT id, opinion_date, topic, view, body, url
           FROM big_v_opinion
           WHERE profile_id=? AND ({' OR '.join(clauses)})
           ORDER BY opinion_date DESC
           LIMIT ?""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]
