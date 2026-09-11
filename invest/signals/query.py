"""trade_signals 只读查询（仪表盘/报告，不落库）。"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date

logger = logging.getLogger(__name__)


def _iso_day(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    s = str(value).strip()
    return s[:10] if s else None


def list_signals(
    conn: sqlite3.Connection,
    asof: date | None = None,
    *,
    horizon: str | None = None,
    session: str | None = None,
    layer: str | None = None,
    severity: str | None = None,
    limit: int = 200,
    date_from: date | str | None = None,
) -> list[dict]:
    """按 date × horizon/session/layer/severity 过滤。asof 缺省为库内最大 date。

    date_from 有值时按区间 [date_from, asof]（asof 缺省不设上界）。
    缺 horizon/layer 列（未 migrate）返回 [] 并打 warning，避免仪表盘炸。
    """
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(trade_signals)")}
        if not cols:
            logger.warning("trade_signals 不存在")
            return []
        if "horizon" not in cols or "layer" not in cols:
            logger.warning("trade_signals 缺 horizon/layer 列，请跑 init_db（SCHEMA 13）")
            return []
        start = _iso_day(date_from)
        sql = (
            "SELECT date, session, signal_id, subject_type, subject, severity, "
            "name, hint, evidence, horizon, layer "
            "FROM trade_signals WHERE 1=1"
        )
        params: list = []
        filt_sql = ""
        filt_params: list = []
        if horizon is not None:
            filt_sql += " AND horizon=?"
            filt_params.append(horizon)
        if session is not None:
            filt_sql += " AND session=?"
            filt_params.append(session)
        if layer is not None:
            filt_sql += " AND layer=?"
            filt_params.append(layer)
        if severity is not None:
            filt_sql += " AND severity=?"
            filt_params.append(severity)
        if start:
            sql += " AND date>=?"
            params.append(start)
            end = _iso_day(asof)
            if end:
                sql += " AND date<=?"
                params.append(end)
        else:
            if asof is None:
                row = conn.execute(
                    "SELECT MAX(date) AS d FROM trade_signals WHERE 1=1" + filt_sql,
                    filt_params,
                ).fetchone()
                day = row["d"] if row else None
            else:
                day = _iso_day(asof)
            if not day:
                return []
            sql += " AND date=?"
            params.append(day)
        sql += filt_sql
        params.extend(filt_params)
        sql += " ORDER BY date, session, signal_id, subject LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(sql, params)]
    except Exception as exc:
        logger.warning("list_signals 失败: %s", exc)
        return []
