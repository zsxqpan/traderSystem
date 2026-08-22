"""数据底座 PIT 化单元测试。用法: python tests/test_pit.py"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.data.pit import (
    CONFLICT,
    DELAYED,
    STALE,
    VALID,
    list_decisions,
    quality_status,
    record_decision,
    record_provenance,
)
from invest.data.storage import upsert_df
from invest.db import connect, init_db


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_pit_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_quality_status():
    p = _tmp_db()
    conn = connect(p)
    today = dt.date.today().isoformat()
    # 空表 -> stale
    status, _ = quality_status(conn, "daily_bars")
    assert status == STALE
    # 今天的数据 -> valid
    upsert_df(conn, "daily_bars", pd.DataFrame([
        {"symbol": "600519", "date": today, "close": 10.0, "src": "akshare"},
    ]))
    status, _info = quality_status(conn, "daily_bars")
    assert status == VALID
    # 5 天前（> 阈值 7*0.5=3.5）-> delayed
    old = (dt.date.today() - dt.timedelta(days=5)).isoformat()
    upsert_df(conn, "daily_bars", pd.DataFrame([
        {"symbol": "600519", "date": old, "close": 10.0, "src": "akshare"},
    ]))
    # 插入一条更旧的不影响 MAX；直接删掉今天的再测
    conn.execute("DELETE FROM daily_bars")
    upsert_df(conn, "daily_bars", pd.DataFrame([
        {"symbol": "600519", "date": old, "close": 10.0, "src": "akshare"},
    ]))
    status, _ = quality_status(conn, "daily_bars")
    assert status == DELAYED
    # 30 天前 -> stale
    very_old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    conn.execute("DELETE FROM daily_bars")
    upsert_df(conn, "daily_bars", pd.DataFrame([
        {"symbol": "600519", "date": very_old, "close": 10.0, "src": "akshare"},
    ]))
    status, _ = quality_status(conn, "daily_bars")
    assert status == STALE
    conn.close()
    print("test_quality_status OK")


def test_quality_conflict():
    p = _tmp_db()
    conn = connect(p)
    today = dt.date.today().isoformat()
    upsert_df(conn, "market_emotion", pd.DataFrame([
        {"date": today, "limit_up_count": 5, "src": "akshare"},
    ]))
    # 正常 -> valid
    status, _ = quality_status(conn, "market_emotion")
    assert status == VALID
    # 最近采集失败 -> conflict
    conn.execute(
        """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
           VALUES('market_emotion','failed',datetime('now','localtime'),datetime('now','localtime'),'x')"""
    )
    status, _info = quality_status(conn, "market_emotion")
    assert status == CONFLICT
    conn.close()
    print("test_quality_conflict OK")


def test_provenance_and_decisions():
    p = _tmp_db()
    conn = connect(p)
    pid = record_provenance(
        conn, "2026-08-15 15:00:00", "600519", object_type="stock",
        reference_id="plan#1", cycle="short", data_version="akshare-qfq", rule_version="v3.1",
    )
    assert pid > 0
    row = conn.execute("SELECT * FROM data_provenance WHERE id=?", (pid,)).fetchone()
    assert row["object_id"] == "600519" and row["cycle"] == "short"
    # 决策留痕
    d1 = record_decision(conn, "reject", "999999", reason="ST 禁止")
    d2 = record_decision(conn, "add", "600519", level="core")
    assert d2 > d1
    rows = list_decisions(conn)
    assert len(rows) == 2
    rows = list_decisions(conn, symbol="999999")
    assert len(rows) == 1 and rows[0]["decision"] == "reject"
    # 非法决策
    try:
        record_decision(conn, "hack", "000001")
        raise AssertionError("should reject invalid decision")
    except ValueError:
        pass
    conn.close()
    print("test_provenance_and_decisions OK")


def test_pool_auto_decision():
    p = _tmp_db()
    conn = connect(p)
    from invest.discipline import pool
    pool.add_to_pool(conn, "000001", level="core", industry="银行")
    pool.remove_from_pool(conn, "000001", note="跌破止损")
    rows = list_decisions(conn, symbol="000001")
    kinds = [r["decision"] for r in rows]
    assert kinds == ["remove", "add"]  # 后写入在前
    conn.close()
    print("test_pool_auto_decision OK")




def test_monthly_date_format():
    import datetime as _dt

    from invest.data.pit import _parse_latest_date
    assert _parse_latest_date("2026年07月份") == _dt.date(2026, 7, 1)
    assert _parse_latest_date("2026年7月") == _dt.date(2026, 7, 1)
    assert _parse_latest_date("2026-07") == _dt.date(2026, 7, 1)
    assert _parse_latest_date("2026-07-01") == _dt.date(2026, 7, 1)
    assert _parse_latest_date("20260701") == _dt.date(2026, 7, 1)
    assert _parse_latest_date("垃圾数据") is None
    print("test_monthly_date_format OK")


if __name__ == "__main__":
    test_quality_status()
    test_quality_conflict()
    test_provenance_and_decisions()
    test_pool_auto_decision()
    test_monthly_date_format()
    print("\nALL PIT TESTS PASSED")
