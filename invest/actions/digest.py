"""盘中动作 digest 文本（阶段 C）。"""
from __future__ import annotations

from invest.actions.format import sort_actions
from invest.actions.types import VERB_CN, Action

DIGEST_LIMIT = 15

_STATUS_CN = {
    "pending": "待办",
    "triggered": "触发",
    "done": "完成",
    "skipped": "跳过",
    "expired": "过期",
}


def _one_line(s: str) -> str:
    return " ".join(str(s or "").replace("建议买入", "").split()).strip("，,；; ")


def format_digest(rows: list) -> str:
    """priority=1 且 pending/triggered，外加任何 triggered。全文最多 15 行。无 LLM。"""
    picked: list[Action] = []
    seen: set[str] = set()
    for a in sort_actions(rows):
        keep = (a.priority == 1 and a.status in ("pending", "triggered")) or a.status == "triggered"
        if not keep or a.symbol in seen:
            continue
        seen.add(a.symbol)
        picked.append(a)
        if len(picked) >= DIGEST_LIMIT - 1:
            break
    if not picked:
        return ""
    lines = ["【动作提醒】"]
    for a in picked:
        if len(lines) >= DIGEST_LIMIT:
            break
        verb = VERB_CN.get(a.verb, a.verb)
        hint = _one_line(a.hint or "")
        ev = a.evidence if isinstance(a.evidence, dict) else {}
        last = ev.get("last", ev.get("close"))
        try:
            sl = float(a.stop_loss) if a.stop_loss is not None else None
            px = float(last) if last is not None else None
        except (TypeError, ValueError):
            sl = px = None
        if sl is not None and sl > 0 and px is not None and px > 0 and px <= sl:
            hint = _one_line(hint.replace("未破", ""))
            mark = f"现价 {px:g} 触及止损 {sl:g}"
            if mark not in hint:
                hint = f"{hint}，{mark}" if hint else mark
        st = _STATUS_CN.get(a.status, a.status)
        lines.append(f"  [{verb} · {st}] {a.symbol}  {hint}".rstrip())
    return "\n".join(lines)
