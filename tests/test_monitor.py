"""P0 监控单元测试。用法: python tests/test_monitor.py"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.monitor import check_data_conflict, check_position_falsify, run_p0_monitor


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_monitor_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_check_position_falsify():
    p = _tmp_db()
    conn = connect(p)
    conn.execute(
        """INSERT INTO trade_plans(symbol, stop_loss, invalid_condition, status, created_at)
           VALUES('000001', 10.0, '', 'active', datetime('now','localtime'))"""
    )
    conn.execute(
        """INSERT INTO trade_plans(symbol, stop_loss, invalid_condition, status, created_at)
           VALUES('600000', 20.0, '证伪', 'active', datetime('now','localtime'))"""
    )
    conn.commit()
    conn.close()
    # 000001 现价 9.5 <= 10 触发止损；600000 证伪条件
    alerts = check_position_falsify(p, {"000001": 9.5, "600000": 21.0})
    kinds = sorted(a["kind"] for a in alerts)
    assert "stop_loss" in kinds and "falsify" in kinds
    # 价格安全 -> 无告警
    assert check_position_falsify(p, {"000001": 11.0}) == []
    print("test_check_position_falsify OK")


def test_check_data_conflict():
    p = _tmp_db()
    # 无 realtime 留痕 -> 数据失效告警
    alerts = check_data_conflict(p)
    assert any(a["kind"] == "data_conflict" for a in alerts)
    # 写入新鲜留痕 -> 无告警
    conn = connect(p)
    with conn:
        conn.execute(
            """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
               VALUES('realtime','ok',datetime('now','localtime'),datetime('now','localtime'),
                      'src=sina n=1 lag_avg=1.0s stale=0 failures={}')"""
        )
    conn.close()
    assert check_data_conflict(p) == []
    print("test_check_data_conflict OK")


def test_run_p0_monitor_mocked():
    p = _tmp_db()
    with mock.patch("invest.monitor.Notifier") as m:
        m.return_value.send_text.return_value = True
        with mock.patch("invest.monitor._in_trading_window", return_value=False):
            n = run_p0_monitor(p)  # 非交易时段休市：行情旧属正常，静默
    assert n == 0  # 不推送数据冲突告警（避免"实时行情不可用"刷屏）
    assert m.return_value.send_text.call_args_list == []
    print("test_run_p0_monitor_mocked OK")


def test_run_p0_monitor_trading_window():
    p = _tmp_db()
    with mock.patch("invest.monitor.Notifier") as m:
        m.return_value.send_text.return_value = True
        with mock.patch("invest.monitor._in_trading_window", return_value=True):
            with mock.patch("invest.monitor.check_data_conflict", return_value=[
                {"kind": "data_conflict", "symbol": "", "msg": "[P0]【数据失效】实时行情不可用"}
            ]):
                n = run_p0_monitor(p)
    assert n == 1  # 交易时段：数据冲突告警正常推送
    assert any("[P0]" in c.args[0] for c in m.return_value.send_text.call_args_list)
    print("test_run_p0_monitor_trading_window OK")


if __name__ == "__main__":
    test_check_position_falsify()
    test_check_data_conflict()
    test_run_p0_monitor_mocked()
    print("\nALL MONITOR TESTS PASSED")
