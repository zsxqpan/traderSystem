"""信号排序、展示文本、表格标签、盘前未消化一行。"""
from __future__ import annotations

import datetime as dt
import sqlite3

from invest.signals.thresholds import DISPLAY_LIMIT
from invest.signals.types import Signal
from invest.signals.universe import watch_symbols

_SEV = {"action": 0, "watch": 1, "info": 2}

_TAGS = {
    "auction_keep_vol": "保量",
    "auction_shrink_diverge": "缩量分歧",
    "shrink_extreme": "极致缩量",
    "high_vol": "高位放量",
    "sector_outlier": "偏离",
}


def pick_signals(signals: list[Signal], limit: int = DISPLAY_LIMIT) -> list[Signal]:
    seen: set[tuple[str, str]] = set()
    uniq: list[Signal] = []
    for s in signals:
        key = (s.id, s.subject)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    uniq.sort(key=lambda s: (_SEV.get(s.severity, 9), s.id, s.subject))
    return uniq[:limit]


def format_signals(signals: list[Signal], limit: int = DISPLAY_LIMIT) -> str:
    picked = pick_signals(signals, limit)
    if not picked:
        return ""
    lines = ["【交易信号】"]
    for s in picked:
        lines.append(f"  [{s.severity}] {s.subject} {s.name}：{s.hint}")
    return "\n".join(lines)


def signal_section(signals: list[Signal], limit: int = DISPLAY_LIMIT) -> dict | None:
    """报告用结构化文本节；无命中返回 None。"""
    text = format_signals(signals, limit)
    if not text:
        return None
    body = text.split("\n", 1)[-1] if "\n" in text else ""
    return {"type": "text", "text": "**【交易信号】**\n" + body}


def tags_for(signals: list[Signal], symbol: str) -> str:
    tags: list[str] = []
    for s in signals:
        if s.subject == symbol and s.id in _TAGS:
            tag = _TAGS[s.id]
            if tag not in tags:
                tags.append(tag)
    return "/".join(tags)


def undigested_actions(conn: sqlite3.Connection, asof: dt.date | None = None) -> str:
    """盘前：昨日 action 且标的仍在监控层 → 一行提示。"""
    asof = asof or dt.date.today()
    try:
        watch = set(watch_symbols(conn))
        row = conn.execute(
            "SELECT MAX(date) AS d FROM trade_signals WHERE severity='action' AND date < ?",
            (asof.isoformat(),),
        ).fetchone()
        if not row or not row["d"]:
            return ""
        hits = conn.execute(
            """SELECT subject, name FROM trade_signals
               WHERE severity='action' AND date=?""",
            (row["d"],),
        ).fetchall()
        kept = [r for r in hits if r["subject"] in watch]
        if not kept:
            return ""
        body = "；".join(f"{r['subject']} {r['name'] or ''}".strip() for r in kept[:5])
        return f"昨日未消化: {body}"
    except Exception:
        return ""
