"""数据底座 PIT 化（TODO 2.1，2026-08-15）。

- 数据质量四状态：有效(valid)/延迟(delayed)/失效(stale)/冲突(conflict) 自动检测；
- 数据溯源：data_provenance 表写入（as_of_time/object_id/reference_id/cycle/data_version/rule_version）；
- 候选决策留存：candidate_decisions 表（add/reject/skip/remove）防选择偏差（v3 15.1）。
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd

# 质量状态
VALID, DELAYED, STALE, CONFLICT = "valid", "delayed", "stale", "conflict"

# 各表数据时点列与新鲜度阈值（自然日）
_TABLE_FRESHNESS = {
    "daily_bars": ("date", 7),       # 日线：7 天内
    "index_bars": ("date", 7),
    "industry_bars": ("date", 7),
    "dragon_tiger": ("date", 7),
    "margin": ("date", 10),
    "macro_series": ("date", 90),    # 宏观月度数据：90 天
    "market_emotion": ("date", 7),
    "industry_valuation": ("date", 10),
    "quant_strength": ("run_date", 7),
    "quant_rotation": ("run_date", 7),
    "quant_temperature": ("run_date", 7),
    "quant_capital": ("run_date", 7),
}


def _parse_latest_date(raw: str):
    """宽松解析日期：支持 YYYY-MM-DD / YYYYMMDD / YYYY年MM月份 / YYYY-MM。"""
    import re as _re
    s = str(raw).strip()
    m = _re.match(r"(\d{4})年(\d{1,2})月份?", s)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), 1)
    m = _re.match(r"(\d{4})-(\d{1,2})$", s)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), 1)
    try:
        parsed = pd.to_datetime(s, format="mixed", errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except (ValueError, TypeError):
        return None


def quality_status(
    conn: sqlite3.Connection,
    table: str,
    as_of: str | None = None,
) -> tuple[str, dict]:
    """单表数据质量四状态检测。

    返回 (状态, 详情)。状态判定：
    - valid:    最新数据时点在阈值内，且源可信度正常；
    - delayed:  最新数据时点在阈值内但接近边界（>50% 阈值）；
    - stale:    最新数据时点超过阈值（数据过期）；
    - conflict: 数据存在（时点正常）但 job_runs 最近一次该表采集失败。
    """
    if table not in _TABLE_FRESHNESS:
        return VALID, {"table": table, "note": "未配置新鲜度阈值"}
    date_col, max_days = _TABLE_FRESHNESS[table]
    as_of = as_of or dt.date.today().isoformat()
    try:
        row = conn.execute(
            f"SELECT MAX({date_col}) d FROM {table}"
        ).fetchone()
        latest = str(row["d"]) if row and row["d"] else None
    except Exception as exc:
        return CONFLICT, {"table": table, "error": str(exc)}
    if latest is None:
        return STALE, {"table": table, "latest": None, "note": "无数据"}
    try:
        latest_date = _parse_latest_date(latest)
        if latest_date is None:
            return CONFLICT, {"table": table, "latest": latest, "note": "日期解析失败"}
        age = (pd.Timestamp(as_of).date() - latest_date).days
    except (ValueError, TypeError):
        return CONFLICT, {"table": table, "latest": latest, "note": "日期解析失败"}

    if age > max_days:
        return STALE, {"table": table, "latest": latest, "age_days": age, "max_days": max_days}
    if age > max_days * 0.5:
        return DELAYED, {"table": table, "latest": latest, "age_days": age, "max_days": max_days}

    # 冲突检测：最近一次采集任务失败
    job = table
    try:
        run = conn.execute(
            "SELECT status FROM job_runs WHERE job=? ORDER BY id DESC LIMIT 1", (job,)
        ).fetchone()
        if run and run["status"] == "failed":
            return CONFLICT, {"table": table, "latest": latest, "age_days": age, "note": "最近采集失败"}
    except Exception:
        pass
    return VALID, {"table": table, "latest": latest, "age_days": age, "max_days": max_days}


def quality_report(conn: sqlite3.Connection, tables: list[str] | None = None) -> dict:
    """全表质量报告：{table: (状态, 详情)}。"""
    tables = tables or list(_TABLE_FRESHNESS.keys())
    return {t: quality_status(conn, t) for t in tables}


def record_provenance(
    conn: sqlite3.Connection,
    as_of_time: str,
    object_id: str,
    object_type: str = "",
    reference_id: str = "",
    cycle: str = "",
    data_version: str = "",
    rule_version: str = "",
    note: str = "",
) -> int:
    """写入数据溯源记录，返回记录 id。"""
    cur = conn.execute(
        """INSERT INTO data_provenance(as_of_time, object_id, object_type, reference_id,
                                      cycle, data_version, rule_version, note)
           VALUES(?,?,?,?,?,?,?,?)""",
        (as_of_time, object_id, object_type, reference_id, cycle, data_version, rule_version, note),
    )
    conn.commit()
    return cur.lastrowid or 0


def record_decision(
    conn: sqlite3.Connection,
    decision: str,
    symbol: str,
    level: str = "",
    industry: str = "",
    reason: str = "",
) -> int:
    """记录候选决策（add/reject/skip/remove），防选择偏差。"""
    if decision not in ("add", "reject", "skip", "remove"):
        raise ValueError("decision 必须为 add/reject/skip/remove")
    cur = conn.execute(
        """INSERT INTO candidate_decisions(decision, symbol, level, industry, reason)
           VALUES(?,?,?,?,?)""",
        (decision, symbol, level, industry, reason),
    )
    conn.commit()
    return cur.lastrowid or 0


def list_decisions(conn: sqlite3.Connection, symbol: str = "", limit: int = 50) -> list[dict]:
    """查询决策留痕（可按标的过滤）。"""
    if symbol:
        rows = conn.execute(
            """SELECT * FROM candidate_decisions WHERE symbol=?
               ORDER BY id DESC LIMIT ?""",
            (symbol, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM candidate_decisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
