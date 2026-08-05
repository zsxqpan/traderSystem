"""评级体系与评级-仓位映射（映射表先回测后校准）。"""
from __future__ import annotations

import sqlite3

from invest.config import load_yaml_config

RATING_KINDS = {
    "macro": ("宽松", "中性", "收紧"),
    "market": ("进攻", "中性", "防守"),
}


def set_rating(
    conn: sqlite3.Connection,
    kind: str,
    value: str,
    basis_json: str = "",
) -> None:
    """写入最新评级（同日覆盖）。kind: macro/market。"""
    if kind not in RATING_KINDS:
        raise ValueError(f"评级类型必须为 {list(RATING_KINDS)}")
    if value not in RATING_KINDS[kind]:
        raise ValueError(f"{kind} 评级值必须为 {RATING_KINDS[kind]}")
    conn.execute(
        """INSERT INTO ratings(date, kind, value, basis_json)
           VALUES(date('now','localtime'), ?, ?, ?)
           ON CONFLICT(date, kind) DO UPDATE SET value=excluded.value, basis_json=excluded.basis_json""",
        (kind, value, basis_json),
    )
    conn.commit()


def get_rating(conn: sqlite3.Connection, kind: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM ratings WHERE kind=? ORDER BY date DESC LIMIT 1", (kind,)
    ).fetchone()
    return dict(row) if row else None


_MARKET_KEY = {"进攻": "attack", "中性": "neutral", "防守": "defense"}
_MACRO_KEY = {"宽松": "loose", "中性": "neutral", "收紧": "tight"}


def get_position_limit(
    conn: sqlite3.Connection,
    macro: str | None = None,
    market: str | None = None,
    config: dict | None = None,
) -> float:
    """按 市场状态×宏观 映射返回总仓位上限（0-1）。"""
    macro = macro or (get_rating(conn, "macro") or {}).get("value")
    market = market or (get_rating(conn, "market") or {}).get("value")
    if macro is None or market is None:
        return 0.5  # 评级缺失时保守默认
    config = config or load_yaml_config()
    mapping = config.get("rating_position_map", {})
    try:
        return float(mapping[_MARKET_KEY[market]][_MACRO_KEY[macro]])
    except (KeyError, TypeError):
        return 0.5