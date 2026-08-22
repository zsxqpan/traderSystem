"""D18 异常波动 skill（量比/振幅/影线信号，仅候选池标的防噪音）。"""
from __future__ import annotations

SKILL = {
    "id": "d18_abnormal_moves",
    "name": "异常波动",
    "kind": "section",
    "description": "异常波动信号（量比/振幅/影线，仅候选池标的）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 5",
    },
}


def render(db_path: str, n: int = 5) -> str:
    from invest.db import connect
    from invest.report import _abnormal_moves

    conn = connect(db_path)
    try:
        rows = _abnormal_moves(conn, n=n)
        return "\n".join(f"{a['symbol']} {a['signal']}（{a['detail']}）" for a in rows)
    finally:
        conn.close()
