"""调度/推送/流水线单元测试。用法: python tests/test_pipeline.py"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from invest.db import connect, init_db
from invest.notifier import Notifier
from invest.scheduler import build_scheduler


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_pipe_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_notifier_disabled_and_mock():
    with mock.patch("invest.notifier.get_settings") as gs:
        gs.return_value.wecom_webhook = ""
        n = Notifier(webhook="")
        assert n.enabled is False
        assert n.send_text("x") is False

    n2 = Notifier(webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake")
    with mock.patch("invest.notifier.requests.post") as m:
        m.return_value.status_code = 200
        assert n2.send_text("hello", key="k1") is True
        assert n2.send_text("dup", key="k1", min_interval=3600) is False  # 限频跳过
    print("test_notifier_disabled_and_mock OK")


def test_scheduler_jobs():
    sched = build_scheduler()
    ids = {j.id for j in sched.get_jobs()}
    assert {"premarket", "after_close", "weekend", "intraday", "monthly", "yearly", "nightly"} <= ids
    print("test_scheduler_jobs OK")


def test_pipeline_quant():
    from invest.pipeline import quant
    p = _tmp_db()
    conn = connect(p)
    n = 400
    dates = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(2)
    closes = pd.DataFrame({
        "A": 100 * np.cumprod(1 + rng.normal(0.002, 0.01, n)),
        "B": 100 * np.cumprod(1 + rng.normal(-0.001, 0.01, n)),
    }, index=dates)
    amounts = pd.DataFrame({"A": 100.0, "B": 100.0}, index=dates)
    rows = []
    for ind, df in closes.items():
        for d, v in df.items():
            rows.append({"date": d.date().isoformat(), "industry": ind, "close": float(v), "amount": float(amounts.loc[d, ind])})
    from invest.data.storage import upsert_df
    upsert_df(conn, "industry_bars", pd.DataFrame(rows))
    upsert_df(conn, "index_bars", pd.DataFrame([
        {"index_code": "000300", "date": d.date().isoformat(), "close": float(100 * np.cumprod(1 + rng.normal(0.0005, 0.008, n))[i])}
        for i, d in enumerate(dates)
    ]))
    conn.close()
    counts = quant(p)
    assert counts["strength"] == 2 and counts["weekly"] == 2
    assert counts["rotation"] == 2 and counts["temperature"] == 1
    conn = connect(p)
    assert conn.execute("SELECT COUNT(*) FROM quant_strength WHERE period='short'").fetchone()[0] == 2
    conn.close()
    print("test_pipeline_quant OK")


def test_notify_messages_no_crash():
    from invest.pipeline import notify_after_close, notify_premarket, notify_weekend
    p = _tmp_db()
    with mock.patch("invest.notifier.Notifier") as m:
        m.return_value.send_text.return_value = True
        assert notify_premarket(p, "test") is True
        assert notify_after_close(p, "test") is True
        assert notify_weekend(p, "test") is True
        texts = [c.args[0] for c in m.return_value.send_text.call_args_list]
        assert all("数据截至" in t for t in texts)
    print("test_notify_messages_no_crash OK")




def test_build_collect_tasks_from_pool():
    from invest.pipeline import build_collect_tasks
    p = _tmp_db()
    conn = connect(p)
    from invest.discipline.pool import add_to_pool
    add_to_pool(conn, "000001")
    add_to_pool(conn, "600000")
    conn.close()
    tasks = build_collect_tasks(p)
    stock_tasks = [t for t in tasks if t["kind"] == "stock_daily_all"]
    assert len(stock_tasks) == 1
    assert stock_tasks[0]["params"]["symbols"] == ["000001", "600000"]
    print("test_build_collect_tasks_from_pool OK")


def test_pipeline_stock_quant():
    from invest.pipeline import quant
    p = _tmp_db()
    conn = connect(p)
    n = 400
    dates = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(8)
    industry_closes = pd.DataFrame({
        "A": 100 * np.cumprod(1 + rng.normal(0.002, 0.01, n)),
        "B": 100 * np.cumprod(1 + rng.normal(-0.001, 0.01, n)),
    }, index=dates)
    industry_amounts = pd.DataFrame({"A": 100.0, "B": 100.0}, index=dates)
    common = rng.normal(0.002, 0.01, n)
    stock_closes = pd.DataFrame({
        "000001": 100 * np.cumprod(1 + common),
        "600000": 100 * np.cumprod(1 + common + rng.normal(0, 0.002, n)),  # 与 000001 高度同步
        "300750": 100 * np.cumprod(1 + rng.normal(-0.002, 0.012, n)),
    }, index=dates)
    rows = []
    for ind, df in industry_closes.items():
        for d, v in df.items():
            rows.append({"date": d.date().isoformat(), "industry": ind, "close": float(v), "amount": float(industry_amounts.loc[d, ind])})
    for sym, df in stock_closes.items():
        for d, v in df.items():
            rows.append({"date": d.date().isoformat(), "symbol": sym, "close": float(v)})
    from invest.data.storage import upsert_df
    upsert_df(conn, "industry_bars", pd.DataFrame([r for r in rows if "industry" in r]))
    upsert_df(conn, "daily_bars", pd.DataFrame([r for r in rows if "symbol" in r]))
    bench = pd.Series(100 * np.cumprod(1 + rng.normal(0.0005, 0.008, n)), index=dates)
    upsert_df(conn, "index_bars", pd.DataFrame([
        {"index_code": "000300", "date": d.date().isoformat(), "close": float(bench.iloc[i])}
        for i, d in enumerate(dates)
    ]))
    conn.close()
    counts = quant(p)
    assert counts.get("stock_strength") == 3
    assert counts.get("stock_weekly") == 3
    assert counts.get("stock_linkage", 0) >= 1
    conn = connect(p)
    n_stock = conn.execute("SELECT COUNT(*) FROM quant_strength WHERE obj_type='stock'").fetchone()[0]
    conn.close()
    assert n_stock == 6  # 3 short + 3 mid
    print("test_pipeline_stock_quant OK")



def test_daily_gainers_and_strength_text():
    """回归：当日涨幅榜 + RS 5/10/20 文本；只取最新 run_date。"""
    from invest import pipeline as pl
    p = _tmp_db()
    conn = connect(p)
    from invest.data.storage import upsert_df
    upsert_df(conn, "industry_bars", pd.DataFrame([
        {"date": "2026-08-03", "industry": "A", "close": 10.0, "src": "akshare"},
        {"date": "2026-08-04", "industry": "A", "close": 11.0, "src": "akshare"},
        {"date": "2026-08-03", "industry": "B", "close": 20.0, "src": "akshare"},
        {"date": "2026-08-04", "industry": "B", "close": 20.5, "src": "akshare"},
    ]))
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-04", "obj_type": "industry", "obj": "A", "period": "short",
         "rs": 0.1, "rs5": 0.05, "rs10": 0.08, "rs20": 0.12, "trend_stage": "启动", "calc_version": "v1"},
        {"run_date": "2026-08-03", "obj_type": "industry", "obj": "B", "period": "short",
         "rs": 0.9, "rs5": 0.9, "rs10": 0.9, "rs20": 0.9, "trend_stage": "加速", "calc_version": "v1"},
    ]))
    gains = pl._top_daily_gainers(conn)
    assert "A +10.00%" in gains and "B +2.50%" in gains
    strength = pl._top_strength(conn, "short")
    assert "A" in strength
    assert "5日+5.0%" in strength and "10日+8.0%" in strength and "20日+12.0%" in strength
    assert "B" not in strength  # 只取最新 run_date
    conn.close()
    print("test_daily_gainers_and_strength_text OK")

if __name__ == "__main__":
    test_notifier_disabled_and_mock()
    test_scheduler_jobs()
    test_pipeline_quant()
    test_notify_messages_no_crash()
    test_build_collect_tasks_from_pool()
    test_pipeline_stock_quant()
    test_daily_gainers_and_strength_text()
    print("\nALL PIPELINE TESTS PASSED")