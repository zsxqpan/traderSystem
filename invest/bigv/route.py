"""飞书文本分流：/大V 必进；口语点名命中名单才进。"""
from __future__ import annotations

import re
import sqlite3

from invest.bigv.ask import ask_big_v
from invest.bigv.watch import list_watched

_CMD = re.compile(r"^[/／]?大[Vv]\s*(\S*)\s*(.*)$")
_NL = (
    re.compile(r"问一下(\S+?)[：:，,\s]+(.+)"),
    re.compile(r"问(\S+?)[：:，,\s]+(.+)"),
    re.compile(r"以(\S+?)的视角[：:，,\s]*(.+)"),
    re.compile(r"用(\S+?)的(?:口吻|视角)[：:，,\s]*(.+)"),
)


def _strip_mention(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^@_user_\d+\s*", "", t)
    t = re.sub(r"^@\S+\s*", "", t)
    return t.strip()


def parse_feishu(text: str) -> dict | None:
    t = _strip_mention(text)
    if not t:
        return None
    infer = any(k in t for k in ("推断", "扮演"))
    mode = "infer" if infer else "grounded"
    if re.match(r"^[/／]大[Vv]\b", t):
        m = _CMD.match(t)
        name = (m.group(1) if m else "").strip()
        rest = (m.group(2) if m else "").strip()
        return {"kind": "command", "name": name, "question": rest, "mode": mode}
    for pat in _NL:
        nm = pat.search(t)
        if nm:
            return {
                "kind": "nl",
                "name": nm.group(1).strip(),
                "question": nm.group(2).strip(),
                "mode": mode,
            }
    return None


def try_feishu(conn: sqlite3.Connection, text: str, *, complete_fn=None) -> str | None:
    """口令必有回复；口语未点中名单返回 None。"""
    parsed = parse_feishu(text)
    if not parsed:
        return None
    names = [w["name"] for w in list_watched(conn)]
    if parsed["kind"] == "command":
        if not parsed["name"]:
            listed = "、".join(names) or "（还没有登记）"
            return f"用法：/大V 姓名 问题\n已登记：{listed}"
        if not parsed["question"]:
            return f"请跟上要问的问题，例如：/大V {parsed['name']} 茅台怎么看"
        out = ask_big_v(
            conn, name=parsed["name"], question=parsed["question"],
            mode=parsed["mode"], complete_fn=complete_fn,
        )
        if not out.get("ok"):
            return out.get("error") or "名单里没有"
        return out["answer"]

    out = ask_big_v(
        conn, name=parsed["name"], question=parsed["question"],
        mode=parsed["mode"], complete_fn=complete_fn,
    )
    if not out.get("ok"):
        err = out.get("error") or ""
        if "名单里没有" in err:
            return None
        return err
    return out["answer"]
