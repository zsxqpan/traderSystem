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
    # 2026-08-18 合并盘后报告：nightly/p2_brief → evening_report（数据滞后时跳过并推送原因）
    assert {"premarket", "morning_brief", "after_close", "snapshot_close", "weekend", "intraday_tick",
            "monthly", "yearly", "industry_refresh", "daily_refresh", "evening_report"} <= ids
    assert "p2_brief" not in ids and "nightly" not in ids
    print("test_scheduler_jobs OK")


def test_ticker_only_and_job_funcs():
    """OS 计划任务模式：ticker_only 只留 10s 轮询；JOB_FUNCS 覆盖全部可迁移任务。"""
    from invest.scheduler import JOB_FUNCS, build_scheduler

    sched = build_scheduler(ticker_only=True)
    assert {j.id for j in sched.get_jobs()} == {"intraday_tick"}
    assert set(JOB_FUNCS) == {
        "premarket", "morning_brief", "after_close", "snapshot_close", "weekend", "monthly", "yearly",
        "industry_refresh", "daily_refresh", "evening_report",
    }
    print("test_ticker_only_and_job_funcs OK")


def test_data_lag_reason():
    """盘后数据新鲜度门禁：空库/旧数据 → 返回滞后原因；已更新到最近交易日 → 返回空串。"""
    import datetime as dt

    from invest.data.calendar import latest_trading_day
    from invest.scheduler import _data_lag_reason

    p = _tmp_db()
    conn = connect(p)
    try:
        # 空库 → 滞后
        assert _data_lag_reason(conn) != ""
        # 写入最近交易日数据 → 新鲜
        exp = latest_trading_day(dt.date.today()).isoformat()
        import pandas as pd

        from invest.data.storage import upsert_df
        upsert_df(conn, "daily_bars", pd.DataFrame([{"date": exp, "symbol": "000001", "close": 10.0}]))
        upsert_df(conn, "index_bars", pd.DataFrame([{"date": exp, "index_code": "000300", "close": 100.0}]))
        assert _data_lag_reason(conn) == ""
        # 只剩上一交易日 → 滞后（注意：今天若为周末，today-1d 可能仍是最近交易日，
        # 2026-08-22 修复：改为取最近交易日的前一个交易日，任何星期几都成立）
        y = latest_trading_day(dt.date.fromisoformat(exp) - dt.timedelta(days=1)).isoformat()
        conn.execute("UPDATE daily_bars SET date=?", (y,))
        conn.execute("UPDATE index_bars SET date=?", (y,))
        conn.commit()
        assert _data_lag_reason(conn) != ""
    finally:
        conn.close()
    print("test_data_lag_reason OK")


def test_data_freshness_and_snapshot_close():
    """2026-08-20：① query_data_freshness 校验数据时点；② snapshot_close 收盘快照落库。"""
    import datetime as dt

    from invest.agent.tools import query_data_freshness
    from invest.data.calendar import latest_trading_day
    from invest.data.storage import upsert_df
    from invest.pipeline import snapshot_close

    p = _tmp_db()
    conn = connect(p)
    # 空库 → 数据滞后
    f = query_data_freshness(conn)
    assert f["fresh"] is False
    # 写入最近交易日日线/指数 → 新鲜
    exp = latest_trading_day(dt.date.today()).isoformat()
    upsert_df(conn, "daily_bars", pd.DataFrame([
        {"symbol": "000001", "date": exp, "close": 10.0, "src": "akshare"},
    ]))
    upsert_df(conn, "index_bars", pd.DataFrame([
        {"index_code": "000300", "date": exp, "close": 100.0, "src": "akshare"},
    ]))
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": exp, "obj_type": "industry", "obj": "A", "period": "short",
         "rs": 0.1, "calc_version": "v1"},
    ]))
    assert query_data_freshness(conn)["fresh"] is True
    conn.close()

    # snapshot_close：交易日 + mock 行情/指数源
    from invest.data.realtime import Quote
    if dt.date.today().weekday() < 5:  # 交易日才写入
        conn = connect(p)
        upsert_df(conn, "candidate_pool", pd.DataFrame([
            {"symbol": "000001", "level": "core", "in_date": exp, "out_date": None},
        ]))
        conn.close()
        class _FakeQuoter:
            source_failures = {"sina": 0, "tencent": 0, "em_push2": 0}
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def fetch(self, symbols):
                return {"sz000001": Quote(symbol="sz000001", price=10.66, ts=dt.datetime.now(), src="sina")}
        with mock.patch("invest.data.realtime.RealtimeQuoter", _FakeQuoter), mock.patch(
                "invest.pipeline._fetch_index_closes",
                return_value=[{"index_code": "000300", "date": exp, "close": 4000.5, "src": "snapshot"}]):
            r = snapshot_close(p)
        assert r.get("stock") == 1 and r.get("index") == 1
        conn = connect(p)
        row = conn.execute(
            "SELECT close FROM daily_bars WHERE symbol='000001' AND date=? AND src='snapshot'", (exp,)
        ).fetchone()
        assert row and abs(float(row["close"]) - 10.66) < 1e-6
        conn.close()
    print("test_data_freshness_and_snapshot_close OK")


def test_evening_report_freshness_gate():
    """晚间盘后报告：数据滞后 → 不发报告只发原因；数据新鲜 → 正常发报告。"""
    import datetime as dt

    from invest.data.calendar import latest_trading_day
    from invest.scheduler import _evening_report

    # 滞后场景
    p_stale = _tmp_db()
    conn = connect(p_stale)
    try:
        with mock.patch("invest.scheduler.Notifier") as m:
            m.return_value.send_text.return_value = True
            _evening_report(p_stale, conn)
        texts = [c.args[0] for c in m.return_value.send_text.call_args_list]
        assert texts and "数据滞后" in texts[0] and "未发送" in texts[0]
    finally:
        conn.close()

    # 新鲜场景
    p_fresh = _tmp_db()
    conn = connect(p_fresh)
    try:
        exp = latest_trading_day(dt.date.today()).isoformat()
        import pandas as pd

        from invest.data.storage import upsert_df
        upsert_df(conn, "daily_bars", pd.DataFrame([{"date": exp, "symbol": "000001", "close": 10.0}]))
        upsert_df(conn, "index_bars", pd.DataFrame([{"date": exp, "index_code": "000300", "close": 100.0}]))
        with mock.patch("invest.scheduler.Notifier") as m:
            m.return_value.send_text.return_value = True
            _evening_report(p_fresh, conn)
        texts = [c.args[0] for c in m.return_value.send_text.call_args_list]
        assert texts and "数据滞后" not in texts[0] and "盘后日报" in texts[0]
    finally:
        conn.close()
    print("test_evening_report_freshness_gate OK")


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



def test_daily_movers_and_strength_text():
    """回归：当日涨/跌幅榜（趋势标签/新晋）+ 宽度 + RS 5/10/20；只取最新 run_date。"""
    from invest import pipeline as pl
    p = _tmp_db()
    conn = connect(p)
    from invest.data.storage import upsert_df
    upsert_df(conn, "industry_bars", pd.DataFrame([
        {"date": "2026-08-02", "industry": "A", "close": 9.0, "src": "akshare"},
        {"date": "2026-08-03", "industry": "A", "close": 10.0, "src": "akshare"},
        {"date": "2026-08-04", "industry": "A", "close": 11.0, "src": "akshare"},
        {"date": "2026-08-02", "industry": "B", "close": 19.0, "src": "akshare"},
        {"date": "2026-08-03", "industry": "B", "close": 20.0, "src": "akshare"},
        {"date": "2026-08-04", "industry": "B", "close": 20.5, "src": "akshare"},
    ]))
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-04", "obj_type": "industry", "obj": "A", "period": "short",
         "rs": 0.1, "rs5": 0.05, "rs10": 0.08, "rs20": 0.12, "trend_stage": "启动", "calc_version": "v1"},
        {"run_date": "2026-08-03", "obj_type": "industry", "obj": "B", "period": "short",
         "rs": 0.9, "rs5": 0.9, "rs10": 0.9, "rs20": 0.9, "trend_stage": "加速", "calc_version": "v1"},
    ]))
    block = pl._daily_movers_block(conn)
    assert "A +10.00% [趋势强/新启动]" in block
    assert "B +2.50%" in block
    assert "当日跌幅前5:" in block
    assert pl._breadth(conn) == "上涨2/下跌0"
    strength = pl._top_strength(conn, "short")
    assert "A" in strength
    assert "5日+5.0%" in strength and "10日+8.0%" in strength and "20日+12.0%" in strength
    assert "B" not in strength  # 只取最新 run_date
    # Agent 观点结构化（结论/周期/失效条件）
    from invest.viewpoints.store import create_viewpoint
    create_viewpoint(conn, source="research", obj="A", conclusion="测试观点", period_tag="short",
                     confidence=0.6, evidence=[{"x": 1}], invalid_condition="RS转负")
    vp = pl._agent_viewpoints(conn)
    assert "测试观点 [短线]（失效:RS转负）" in vp
    conn.close()
    print("test_daily_movers_and_strength_text OK")

if __name__ == "__main__":
    test_notifier_disabled_and_mock()
    test_scheduler_jobs()
    test_ticker_only_and_job_funcs()
    test_data_freshness_and_snapshot_close()
    test_data_lag_reason()
    test_evening_report_freshness_gate()
    test_pipeline_quant()
    test_notify_messages_no_crash()
    test_build_collect_tasks_from_pool()
    test_pipeline_stock_quant()
    test_daily_movers_and_strength_text()
    print("\nALL PIPELINE TESTS PASSED")