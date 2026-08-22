"""D27 消息汇总 skill（2026-08-22 新增：宏观[仅变化时]/个股/市场外 + 影响解读）。"""
from __future__ import annotations

SKILL = {
    "id": "d27_news_digest",
    "name": "消息汇总",
    "kind": "section",
    "description": "昨收后关键消息汇总（宏观仅变化时提示/个股/市场外）+ 影响与机会解读",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def _fmt_group(title: str, items: list[dict]) -> list[str]:
    if not items:
        return []
    return [f"【{title}】"] + [f"  - {it.get('title', '')}｜{it.get('impact', '')}" for it in items]


def render(db_path: str) -> str:
    from invest.skills.sections._digest import digest

    d = digest(db_path)
    if not d.get("ok"):
        return "（暂无消息素材或汇总失败）"
    lines: list[str] = []
    news = d.get("news") or {}
    if d.get("macro_changed"):
        lines.extend(_fmt_group("宏观", news.get("macro") or []))
    lines.extend(_fmt_group("个股", news.get("stock") or []))
    lines.extend(_fmt_group("市场外", news.get("market_outside") or []))
    return "\n".join(lines)
