"""信号排序、展示文本、表格标签、盘前未消化一行。"""
from __future__ import annotations

import datetime as dt
import sqlite3

from invest.signals.thresholds import DISPLAY_B1_DISCOVERY_ACTION, DISPLAY_LIMIT
from invest.signals.types import Signal
from invest.signals.universe import watch_symbols

_SEV = {"action": 0, "watch": 1, "info": 2}
_SEV_CN = {"action": "行动", "watch": "观察", "info": "提示"}

_TAGS = {
    "auction_keep_vol": "保量",
    "auction_shrink_diverge": "缩量分歧",
    "shrink_extreme": "极致缩量",
    "high_vol": "高位放量",
    "sector_outlier": "偏离",
}


def pick_signals(
    signals: list[Signal],
    limit: int = DISPLAY_LIMIT,
    *,
    layers: list[str] | None = None,
    horizon: str | None = None,
    severities: list[str] | None = None,
) -> list[Signal]:
    """在现有 (id,subject) 去重 + action 优先 之上加过滤。"""
    filtered: list[Signal] = []
    for s in signals:
        if horizon is not None and s.horizon != horizon:
            continue
        if layers is not None and s.layer not in layers:
            continue
        if severities is not None and s.severity not in severities:
            continue
        filtered.append(s)
    seen: set[tuple[str, str]] = set()
    uniq: list[Signal] = []
    for s in filtered:
        key = (s.id, s.subject)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    uniq.sort(key=lambda s: (_SEV.get(s.severity, 9), s.id, s.subject))
    return uniq[:limit]


def split_b1(signals: list[Signal]) -> tuple[list[Signal], list[Signal]]:
    """主节 = watch∪market 的 short；发现节 = discovery 且 action 的 short，最多 DISPLAY_B1_DISCOVERY_ACTION。"""
    main = [s for s in signals if s.horizon == "short" and s.layer in ("watch", "market")]
    disc = [
        s for s in signals
        if s.horizon == "short" and s.layer == "discovery" and s.severity == "action"
    ]
    return main, pick_signals(disc, DISPLAY_B1_DISCOVERY_ACTION)


def format_signals(
    signals: list[Signal],
    limit: int = DISPLAY_LIMIT,
    title: str = "【交易信号】",
) -> str:
    picked = pick_signals(signals, limit)
    if not picked:
        return ""
    lines = [title]
    for s in picked:
        sev = _SEV_CN.get(s.severity, s.severity)
        name = (s.name or "").strip()
        head = f"{s.subject} · {name}" if name else s.subject
        lines.append(f"  [{sev}] {head}：{s.hint}")
    return "\n".join(lines)


def format_discovery_action(signals: list[Signal]) -> str:
    """无命中返回空串。标题【明确发现】。"""
    _, disc = split_b1(signals)
    if not disc:
        return ""
    return format_signals(disc, limit=DISPLAY_B1_DISCOVERY_ACTION, title="【明确发现】")


def signal_section(
    signals: list[Signal],
    limit: int = DISPLAY_LIMIT,
    title: str = "【交易信号】",
) -> dict | None:
    """报告用结构化文本节；无命中返回 None。"""
    text = format_signals(signals, limit, title=title)
    if not text:
        return None
    body = text.split("\n", 1)[-1] if "\n" in text else ""
    return {"type": "text", "text": f"**{title}**\n\n" + body}


_QUAD_GROUPS = (
    ("quad_hunt", "主战场"),
    ("quad_chase", "追高风险"),
    ("quad_watch_cheap", "观察"),
    ("quad_avoid", "回避"),
)


def format_mid_quadrants(signals: list[Signal]) -> str:
    """按 hunt/chase/watch_cheap/avoid 分组；某组空则跳过；全空返回空串。标题【中线战场】。"""
    lines = ["【中线战场】"]
    any_g = False
    for sid, title in _QUAD_GROUPS:
        items = [s for s in signals if s.id == sid]
        if not items:
            continue
        any_g = True
        lines.append(f"  · {title}")
        for s in items:
            lines.append(f"    {s.subject}：{s.hint}")
    if not any_g:
        return ""
    return "\n".join(lines)


def tags_for(signals: list[Signal], symbol: str, *, layers: list[str] | None = None) -> str:
    tags: list[str] = []
    for s in signals:
        if layers is not None and s.layer not in layers:
            continue
        if s.subject == symbol and s.id in _TAGS:
            tag = _TAGS[s.id]
            if tag not in tags:
                tags.append(tag)
    return "/".join(tags)


def rows_to_signals(rows: list[dict] | None) -> list[Signal]:
    """list_signals 行 → Signal（evidence 可为 JSON 字符串）。"""
    import json

    out: list[Signal] = []
    for r in rows or []:
        ev = r.get("evidence") or {}
        if isinstance(ev, str):
            try:
                ev = json.loads(ev) if ev else {}
            except ValueError:
                ev = {}
        if not isinstance(ev, dict):
            ev = {}
        out.append(Signal(
            id=str(r.get("signal_id") or r.get("id") or ""),
            name=str(r.get("name") or ""),
            session=str(r.get("session") or "daily"),
            severity=str(r.get("severity") or "watch"),
            subject_type=str(r.get("subject_type") or "sector"),
            subject=str(r.get("subject") or ""),
            hint=str(r.get("hint") or ""),
            evidence=ev,
            horizon=str(r.get("horizon") or "short"),
            layer=str(r.get("layer") or "watch"),
        ))
    return out


def format_market_opportunity(signals: list[Signal]) -> str:
    """四象限 + resonance + emotion_stage_shift；空则空串。标题【市场机会（规则）】。"""
    quad = format_mid_quadrants(signals)
    extra = [s for s in signals if s.id in ("sector_resonance", "emotion_stage_shift")]
    if not quad and not extra:
        return ""
    lines = ["【市场机会（规则）】"]
    if quad:
        body = quad.split("\n", 1)[-1] if quad.startswith("【中线战场】") else quad
        if body:
            lines.append(body)
    for s in extra:
        lines.append(f"  {s.name} {s.subject}：{s.hint}")
    return "\n".join(lines)


def undigested_actions(conn: sqlite3.Connection, asof: dt.date | None = None) -> str:
    """盘前：昨日 severity=action 一行提示，标注池内/池外。"""
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
        if not hits:
            return ""
        parts = []
        for r in hits[:5]:
            loc = "池内" if r["subject"] in watch else "池外"
            name = (r["name"] or "").strip()
            parts.append(f"{r['subject']} {name}（{loc}）".replace("  ", " ").strip())
        body = "；".join(parts)
        return f"昨日未消化: {body}"
    except Exception:
        return ""
