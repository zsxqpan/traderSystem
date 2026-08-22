"""D26 涨停异动监控 skill（2026-08-22 新增：停牌 + 风险提示/异动监控/暴雷）。

文本视图供测试/文档；表格结构由 a0 盘前报告组装（列：类型/标的/事件/影响）。
"""
from __future__ import annotations

SKILL = {
    "id": "d26_market_watch",
    "name": "涨停异动监控",
    "kind": "section",
    "description": "停牌 + 风险提示/异动监控/暴雷（业绩雷/司法雷/黑天鹅）+ 精简风险提示",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.data.halt import fetch_halt_list
    from invest.skills.sections._digest import digest

    lines: list[str] = []
    halts = fetch_halt_list()
    if halts:
        lines.append(f"停牌 {len(halts)} 家: " +
                     "、".join(f"{h['name']}({h['symbol']})" for h in halts[:8]))
    d = digest(db_path)
    for it in (d.get("risk_items") or []):
        lines.append(
            f"{it.get('kind', '')} {it.get('name', '')}({it.get('symbol', '')}): "
            f"{it.get('event', '')}"
        )
    if d.get("risk_summary"):
        lines.append(f"风险提示: {d['risk_summary']}")
    return "\n".join(lines)
