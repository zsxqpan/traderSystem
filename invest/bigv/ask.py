"""以指定大V 视角回答：默认有据，可切推断。"""
from __future__ import annotations

import logging
import sqlite3

from invest.bigv.search import search_opinions
from invest.bigv.watch import list_watched, resolve_name

logger = logging.getLogger(__name__)

_NO_ADVICE = ("建议买入",)


def _scrub_advice(text: str) -> str:
    out = text
    for ban in _NO_ADVICE:
        if ban in out:
            out = out.replace(ban, "仅复述其公开看法")
    return out


def _cite_block(citations: list[dict]) -> str:
    if not citations:
        return ""
    lines = ["", "依据："]
    for c in citations:
        bit = " ".join(x for x in (c.get("date"), c.get("topic"), c.get("url")) if x)
        lines.append(f"- {bit}")
    return "\n".join(lines)


def _fallback(name: str, hits: list[dict], mode: str) -> str:
    if mode == "infer" and not hits:
        return f"按{name}一贯的说话方式推断，他没公开谈过这个题目，只能从风格外推。"
    parts = [f"按{name}公开说过的："]
    for h in hits:
        snippet = (h.get("body") or h.get("view") or "")[:160]
        parts.append(f"- {h.get('opinion_date','')} {snippet}")
    return "\n".join(parts)


def _llm_complete(conn: sqlite3.Connection, system: str, user: str) -> str:
    try:
        from invest.agent.llm import LLMClient

        return (LLMClient(conn).run(system, user, tools=None, job="bigv_ask", max_turns=1) or "").strip()
    except Exception as exc:
        logger.warning("大V 问答 LLM 失败: %s", exc)
        return ""


def ask_big_v(
    conn: sqlite3.Connection,
    *,
    question: str,
    name: str = "",
    profile_id: str = "",
    mode: str = "grounded",
    complete_fn=None,
) -> dict:
    mode = "infer" if mode == "infer" else "grounded"
    q = (question or "").strip()
    resolved = resolve_name(conn, name=name, profile_id=profile_id)
    if resolved["status"] == "missing":
        names = "、".join(w["name"] for w in list_watched(conn)) or "（空）"
        return {"ok": False, "error": f"名单里没有「{name or profile_id}」。已登记：{names}"}
    if resolved["status"] == "ambiguous":
        label = " / ".join(p["name"] for p in resolved["profiles"])
        return {"ok": False, "error": f"重名，请选：{label}", "candidates": resolved["profiles"]}
    profile = resolved["profile"]
    pid = profile["id"]
    hits = search_opinions(conn, pid, q) if q else []
    if mode == "grounded" and not hits:
        return {
            "ok": True,
            "mode": mode,
            "profile_id": pid,
            "answer": f"{profile['name']}没有公开谈过这件事（语料里没有足够相关的原文）。",
            "citations": [],
            "empty_reason": "no_hit",
        }

    card = (profile.get("persona_card") or "").strip()
    ev_lines = []
    for h in hits:
        ev_lines.append(
            f"[{h.get('opinion_date','')}] {h.get('topic') or ''} {h.get('url') or ''}\n"
            f"{(h.get('body') or h.get('view') or '')[:800]}"
        )
    evidence = "\n\n".join(ev_lines) or "（无命中原文）"
    if mode == "grounded":
        system = (
            f"你现在以{profile['name']}的口吻回答。只能依据下面原文，不要编造他没说过的立场。"
            f"禁止输出「建议买入」，这不是交易指令。"
            f"人格卡（仅供口吻）：{card or '无'}"
        )
        user = f"问题：{q}\n\n原文：\n{evidence}"
    else:
        system = (
            f"你现在以{profile['name']}的口吻回答。可以按人格卡外推，但外推必须写「推断」。"
            f"禁止输出「建议买入」，这不是交易指令。"
            f"人格卡：{card or '无'}"
        )
        user = f"问题：{q}\n\n可参考原文：\n{evidence}"

    text = ""
    if complete_fn:
        try:
            text = (complete_fn(system, user) or "").strip()
        except Exception as exc:
            logger.warning("大V complete_fn 失败: %s", exc)
    elif hits or mode == "infer":
        text = _llm_complete(conn, system, user)
    if not text:
        text = _fallback(profile["name"], hits, mode)
    if mode == "infer" and "推断" not in text:
        text = text.rstrip() + "\n\n（以上含推断，不是原文原话。）"
    text = _scrub_advice(text)

    citations = [
        {"date": h.get("opinion_date") or "", "url": h.get("url") or "",
         "topic": h.get("topic") or ""}
        for h in hits
    ]
    answer = text + _cite_block(citations)
    return {
        "ok": True,
        "mode": mode,
        "profile_id": pid,
        "answer": answer,
        "citations": citations,
    }
