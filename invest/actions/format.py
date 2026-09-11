"""动作清单展示文本 / 表格 / b1 子集。"""
from __future__ import annotations

from invest.actions.types import VERB_CN, Action


def _as_action(row) -> Action:
    if isinstance(row, Action):
        return row
    ev = row.get("evidence") or {}
    if isinstance(ev, str):
        import json
        try:
            ev = json.loads(ev) if ev else {}
        except ValueError:
            ev = {}
    return Action(
        date=row.get("date") or "",
        symbol=row.get("symbol") or "",
        name=row.get("name") or "",
        verb=row.get("verb") or "hold",
        priority=int(row.get("priority") or 2),
        source=row.get("source") or "",
        source_ref=row.get("source_ref") or "",
        entry_lo=row.get("entry_lo"),
        entry_hi=row.get("entry_hi"),
        stop_loss=row.get("stop_loss"),
        target=row.get("target"),
        invalid_condition=row.get("invalid_condition") or "",
        position_hint=row.get("position_hint") or "",
        status=row.get("status") or "pending",
        hint=row.get("hint") or "",
        evidence=ev if isinstance(ev, dict) else {},
    )


def sort_actions(rows: list) -> list[Action]:
    acts = [_as_action(r) for r in rows]
    acts.sort(key=lambda a: (a.priority, a.symbol))
    return acts


def pick_b1(rows: list) -> list[Action]:
    """priority 1–2，外加任何 triggered。"""
    out: list[Action] = []
    seen: set[str] = set()
    for a in sort_actions(rows):
        keep = a.priority in (1, 2) or a.status == "triggered"
        if not keep or a.symbol in seen:
            continue
        seen.add(a.symbol)
        out.append(a)
    return out


def _rng(a: Action) -> str:
    if a.entry_lo is not None and a.entry_hi is not None:
        return f"{a.entry_lo:g}-{a.entry_hi:g}"
    return "-"


def _num(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return "-"


def format_actions(rows: list, title: str = "【明日动作（规则）】") -> str:
    acts = sort_actions(rows)
    if not acts:
        return ""
    lines = [title]
    cur_p = None
    labels = {1: "必须", 2: "计划内", 3: "信号", 4: "探索"}
    for a in acts:
        if a.priority != cur_p:
            cur_p = a.priority
            lines.append(f"  — {labels.get(cur_p, '')} —")
        verb = VERB_CN.get(a.verb, a.verb)
        hint = (a.hint or "").replace("建议买入", "")
        lines.append(
            f"  [{verb}] {a.symbol}  区间 {_rng(a)}  止损 {_num(a.stop_loss)}  "
            f"目标 {_num(a.target)}：{hint}"
        )
    return "\n".join(lines)


def action_table(rows: list, *, title: str = "明日动作（规则）",
                 extra_cols: list[str] | None = None) -> dict | None:
    acts = sort_actions(rows)
    if not acts:
        return None
    cols = ["动作", "代码", "区间", "止损", "目标", "含义"]
    extra_cols = extra_cols or []
    table_rows = []
    for a in acts:
        row = [
            VERB_CN.get(a.verb, a.verb),
            a.symbol,
            _rng(a),
            _num(a.stop_loss),
            _num(a.target),
            (a.hint or "").replace("建议买入", ""),
        ]
        table_rows.append(row)
    return {"type": "table", "title": title, "columns": cols + extra_cols, "rows": table_rows}


def range_gap(price: float | None, entry_lo, entry_hi, stop_loss) -> str:
    if price is None:
        return "-"
    try:
        px = float(price)
    except (TypeError, ValueError):
        return "-"
    if entry_lo is not None and entry_hi is not None:
        lo, hi = float(entry_lo), float(entry_hi)
        if lo <= px <= hi:
            return "区间内"
        if px < lo and px > 0:
            return f"距下沿{(lo - px) / px:.1%}"
        if px > hi and px > 0:
            return f"距上沿{(px - hi) / px:.1%}"
    if stop_loss is not None and px > 0:
        return f"距止损{(px - float(stop_loss)) / px:.1%}"
    return "-"
