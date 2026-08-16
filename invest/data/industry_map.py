"""个股→行业映射持久化（TODO [A]2，2026-08-15）。

东财成分接口被拦时用手工核心股映射兜底：`data/industry_stocks.json`。
查询顺序：手工映射表 → candidate_pool.industry（人工维护过的最新值）→ 空。
提供 load / save / industry_of / stocks_of 等接口，供卡片、价差、风险簇复用。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "industry_stocks.json"


def _default_mapping() -> dict[str, str]:
    return {}


def load_industry_stocks(path: Path | str | None = None) -> dict[str, str]:
    """读取手工映射：{symbol: industry}。文件缺失或损坏返回空表。"""
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return _default_mapping()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return _default_mapping()
    mapping = data.get("mapping", data) if isinstance(data, dict) else {}
    if not isinstance(mapping, dict):
        return _default_mapping()
    return {str(k).strip(): str(v).strip() for k, v in mapping.items() if v}


def save_industry_stocks(mapping: dict[str, str], path: Path | str | None = None) -> None:
    """持久化手工映射（保留 note/updated_at 头）。"""
    p = Path(path) if path else DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {}
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (ValueError, OSError):
            doc = {}
    doc = {k: v for k, v in doc.items() if k not in ("mapping",)}
    import datetime as dt
    doc["updated_at"] = dt.date.today().isoformat()
    doc["mapping"] = {str(k): str(v) for k, v in mapping.items() if v}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


def industry_of(conn: sqlite3.Connection | None, symbol: str, mapping: dict | None = None) -> str:
    """查询标的行业：手工映射 → candidate_pool.industry → 空串。

    mapping: 传入则直接使用（避免反复读文件）；None 时读取默认表。
    """
    symbol = str(symbol).strip()
    m = mapping if mapping is not None else load_industry_stocks()
    if symbol in m:
        return m[symbol]
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT industry FROM candidate_pool WHERE symbol=? AND industry IS NOT NULL AND industry != ''",
                (symbol,),
            ).fetchone()
            if row and row["industry"]:
                return str(row["industry"])
        except Exception:  # noqa: BLE001
            pass
    return ""


def stocks_of(industry: str, mapping: dict | None = None) -> list[str]:
    """反向查询：行业 → 标的列表（手工映射内）。"""
    m = mapping if mapping is not None else load_industry_stocks()
    return sorted(s for s, ind in m.items() if ind == industry)


def ensure_pool_industries(conn: sqlite3.Connection, mapping: dict | None = None) -> int:
    """把候选池中 industry 为空的标的用映射表补上（返回补写条数）。"""
    m = mapping if mapping is not None else load_industry_stocks()
    rows = conn.execute(
        "SELECT symbol FROM candidate_pool WHERE out_date IS NULL AND (industry IS NULL OR industry = '')"
    ).fetchall()
    n = 0
    for r in rows:
        ind = m.get(str(r["symbol"]).strip())
        if ind:
            conn.execute(
                "UPDATE candidate_pool SET industry=? WHERE symbol=?",
                (ind, r["symbol"]),
            )
            n += 1
    if n:
        conn.commit()
    return n
