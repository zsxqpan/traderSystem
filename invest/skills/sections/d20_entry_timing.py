"""D20 建仓时机 skill（情绪周期 + 温度 + 低估值候选 → 建仓窗口提示）。"""
from __future__ import annotations

SKILL = {
    "id": "d20_entry_timing",
    "name": "建仓时机",
    "kind": "section",
    "description": "建仓时机提示（情绪周期/温度/低估值+强度启动候选）",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}


def render(db_path: str) -> str:
    from invest.db import connect
    from invest.report import _entry_timing_hints

    conn = connect(db_path)
    try:
        return "\n".join(_entry_timing_hints(conn))
    finally:
        conn.close()
