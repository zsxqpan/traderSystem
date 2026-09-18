"""D26 涨停异动监控 skill（2026-08-22 新增：停牌 + 风险提示/异动监控/暴雷）。

文本视图供测试/文档；表格结构由 a0 盘前报告组装（列：类型/标的/事件/影响）。

2026-09-18：风险条目**只保留个股相关**——LLM 汇总会掺进债券/汇率/商品/宏观政策等
条目（原先一并进「涨停异动监控」表，与"涨停异动"语境不符），统一用 `is_stock_risk` 过滤。
"""
from __future__ import annotations

import re

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

_CODE_RE = re.compile(r"^\d{6}$")
# 明显非个股的标的/事件关键词（债券、汇率、商品、宏观、指数…）
_NON_STOCK_KW = (
    "债", "国债", "城投", "收益率", "汇率", "美元", "人民币", "原油", "黄金", "白银",
    "商品", "期货", "指数", "大盘", "宏观", "GDP", "CPI", "PPI", "PMI", "美联储",
    "央行", "利率", "降准", "降息", "政策", "板块", "行业", "ETF", "基金", "汇率",
)


def is_stock_risk(item: dict) -> bool:
    """风险条目是否属于**个股**：有 6 位股票代码；或名称像公司名且不含非个股关键词。

    取不到标的（名称与代码都空）→ 视为非个股，不进表。
    """
    symbol = str(item.get("symbol") or "").strip()
    name = str(item.get("name") or "").strip()
    if symbol:
        return bool(_CODE_RE.match(symbol))
    if not name:
        return False
    return not any(kw in name for kw in _NON_STOCK_KW)


def stock_risk_items(digest: dict | None) -> list[dict]:
    """从 digest 里挑出个股风险条目（保持原顺序）。"""
    return [it for it in ((digest or {}).get("risk_items") or []) if is_stock_risk(it)]


def render(db_path: str) -> str:
    from invest.data.halt import fetch_halt_list
    from invest.skills.sections._digest import digest

    lines: list[str] = []
    halts = fetch_halt_list()
    if halts:
        lines.append(f"停牌 {len(halts)} 家: " +
                     "、".join(f"{h['name']}({h['symbol']})" for h in halts[:8]))
    d = digest(db_path)
    for it in stock_risk_items(d):
        lines.append(
            f"{it.get('kind', '')} {it.get('name', '')}({it.get('symbol', '')}): "
            f"{it.get('event', '')}"
        )
    if d.get("risk_summary"):
        lines.append(f"风险提示: {d['risk_summary']}")
    return "\n".join(lines)
