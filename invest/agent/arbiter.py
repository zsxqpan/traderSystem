"""矛盾仲裁：识别方向冲突观点，按'尊重市场'原则仲裁并存档分歧。"""
from __future__ import annotations

import sqlite3

from .llm import LLMClient
from invest.viewpoints.store import create_viewpoint

_BULL = ("看多", "向上", "乐观", "超配", "加仓", "走强", "上行")
_BEAR = ("看空", "向下", "悲观", "低配", "减仓", "走弱", "下行")


def extract_direction(conclusion: str) -> str:
    text = conclusion or ""
    if any(k in text for k in _BULL):
        return "bull"
    if any(k in text for k in _BEAR):
        return "bear"
    return "neutral"


def find_conflicts(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """同一标的、同一周期、方向相反且均为 active 的观点对。"""
    rows = conn.execute(
        """SELECT id, obj, period_tag, conclusion FROM viewpoints
           WHERE status='active' AND source IN ('research','trade')
             AND obj IS NOT NULL AND obj != ''"""
    ).fetchall()
    buckets: dict = {}
    for r in rows:
        buckets.setdefault((r["obj"], r["period_tag"]), []).append(r)
    pairs = []
    for group in buckets.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                da, db = extract_direction(a["conclusion"]), extract_direction(b["conclusion"])
                if {da, db} == {"bull", "bear"}:
                    pairs.append((a["id"], b["id"]))
    return pairs


def arbitrate(conn: sqlite3.Connection, vid_a: int, vid_b: int, job: str = "arbiter") -> dict:
    """仲裁一对冲突观点，写入仲裁观点并把分歧存档。"""
    a = conn.execute("SELECT * FROM viewpoints WHERE id=?", (vid_a,)).fetchone()
    b = conn.execute("SELECT * FROM viewpoints WHERE id=?", (vid_b,)).fetchone()
    if a is None or b is None:
        raise ValueError("观点不存在")
    a, b = dict(a), dict(b)
    prompt = (
        f"观点A（{a['source']}）: {a['conclusion']} | 依据: {a['evidence_json']} | 失效条件: {a['invalid_condition']}\n"
        f"观点B（{b['source']}）: {b['conclusion']} | 依据: {b['evidence_json']} | 失效条件: {b['invalid_condition']}\n"
        "原则：尊重市场——市场走势的证据权重高于逻辑推演。请给出仲裁结论（明确方向），并说明理由。"
    )
    client = LLMClient(conn)
    verdict = client.run(
        "你是矛盾仲裁 Agent。输出仲裁结论：方向 + 一句话理由。",
        prompt,
        job=job,
    )
    vid = create_viewpoint(
        conn,
        source="arbiter",
        obj_type=a.get("obj_type") or "",
        obj=a.get("obj") or "",
        conclusion=verdict,
        period_tag=a.get("period_tag") or "short",
        confidence=0.5,
        evidence=[{"viewpoint_a": vid_a, "viewpoint_b": vid_b}],
        invalid_condition="市场走势反转时仲裁失效",
    )
    note = "分歧存档：与对立方仲裁中，待跟踪"
    conn.execute("UPDATE viewpoints SET status='pending_review', review_note=? WHERE id IN (?,?)", (note, vid_a, vid_b))
    conn.commit()
    return {"arbiter_viewpoint_id": vid, "conflict": (vid_a, vid_b), "verdict": verdict}