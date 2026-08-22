"""D24 隔夜外围 skill（美股/A50/日韩/商品/汇率，2026-08-22 新增，含提前开盘日韩）。"""
from __future__ import annotations

SKILL = {
    "id": "d24_global_snapshot",
    "name": "隔夜外围",
    "kind": "section",
    "description": "隔夜外围快照文本（美股/富时A50/日经/韩综/商品/汇率，失败项省略）",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.data.global_snapshot import global_snapshot_rows

    rows = global_snapshot_rows()
    parts = []
    for r in rows:
        if r.get("pct") is not None:
            parts.append(f"{r['name']}{r['pct']:+.2f}%")
        elif r.get("value"):
            parts.append(f"{r['name']} {r['value']:.4f}")
    return " ".join(parts)
