"""候选池 + 关注度分级：核心关注 ≤10，候选池总容量 ≤20。"""
from __future__ import annotations

import sqlite3

LEVELS = ("core", "track", "rest")
POOL_LIMIT = 20
CORE_LIMIT = 10


def _active_rows(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM candidate_pool WHERE out_date IS NULL ORDER BY in_date"
    ).fetchall()


def add_to_pool(
    conn: sqlite3.Connection,
    symbol: str,
    level: str = "track",
    industry: str = "",
    reason: str = "",
    target_value_range: str = "",
    falsify_condition: str = "",
) -> dict:
    """入池；校验关注度级别与容量上限。已入池则更新级别/行业/理由。"""
    if level not in LEVELS:
        raise ValueError(f"关注度级别必须为 {LEVELS}")
    active = _active_rows(conn)
    existing = next((r for r in active if r["symbol"] == symbol), None)
    if existing is None and len(active) >= POOL_LIMIT:
        raise ValueError(f"候选池已满（上限 {POOL_LIMIT}）")
    if existing is None and level == "core" and len([r for r in active if r["level"] == "core"]) >= CORE_LIMIT:
        raise ValueError(f"核心关注已满（上限 {CORE_LIMIT}）")

    if existing is not None:
        conn.execute(
            """UPDATE candidate_pool SET level=?, industry=?, reason=?, target_value_range=?, falsify_condition=?
               WHERE symbol=?""",
            (level, industry, reason, target_value_range, falsify_condition, symbol),
        )
    else:
        conn.execute(
            """INSERT INTO candidate_pool(symbol, level, industry, reason, target_value_range, falsify_condition, in_date)
               VALUES(?,?,?,?,?,?, date('now','localtime'))""",
            (symbol, level, industry, reason, target_value_range, falsify_condition),
        )
    conn.commit()
    try:
        from invest.data.pit import record_decision
        record_decision(
            conn,
            decision="add",
            symbol=symbol,
            level=level,
            industry=industry,
            reason="入池" if existing is None else f"更新级别→{level}",
        )
    except Exception:  # noqa: BLE001
        pass
    return {"symbol": symbol, "level": level, "industry": industry}


def remove_from_pool(conn: sqlite3.Connection, symbol: str, note: str = "") -> None:
    """移出候选池（软删除：写 out_date）。"""
    cur = conn.execute(
        """UPDATE candidate_pool SET out_date = date('now','localtime'), reason = reason || ' | 退出:' || ?
           WHERE symbol=? AND out_date IS NULL""",
        (note, symbol),
    )
    conn.commit()
    try:
        from invest.data.pit import record_decision
        record_decision(conn, decision="remove", symbol=symbol, reason=note or "移出候选池")
    except Exception:  # noqa: BLE001
        pass
    if cur.rowcount == 0:
        raise ValueError(f"{symbol} 不在候选池中")


def list_pool(conn: sqlite3.Connection, active_only: bool = True) -> list[dict]:
    if active_only:
        return [dict(r) for r in _active_rows(conn)]
    rows = conn.execute("SELECT * FROM candidate_pool ORDER BY in_date").fetchall()
    return [dict(r) for r in rows]