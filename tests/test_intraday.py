"""盘中异动监测单元测试。用法: python tests/test_intraday.py"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.db import connect, init_db
from invest.intraday import _in_trading_window, check_core_moves, send_alerts


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_intraday_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    conn = connect(p)
    from invest.data.storage import upsert_df
    upsert_df(conn, "candidate_pool", pd.DataFrame([
        {"symbol": "000001", "level": "core", "in_date": "2026-08-01", "out_date": None},
        {"symbol": "600000", "level": "track", "in_date": "2026-08-01", "out_date": None},
    ]))
    upsert_df(conn, "daily_bars", pd.DataFrame([
        {"symbol": "000001", "date": "2026-08-03", "close": 10.0, "src": "akshare"},
        {"symbol": "600000", "date": "2026-08-03", "close": 20.0, "src": "akshare"},
    ]))
    conn.close()
    return p


def test_trading_window():
    assert _in_trading_window(dt.datetime(2026, 8, 3, 10, 0)) is True
    assert _in_trading_window(dt.datetime(2026, 8, 3, 9, 20)) is False
    assert _in_trading_window(dt.datetime(2026, 8, 3, 11, 40)) is False
    assert _in_trading_window(dt.datetime(2026, 8, 3, 13, 30)) is True
    assert _in_trading_window(dt.datetime(2026, 8, 8, 10, 0)) is False  # 周六
    print("test_trading_window OK")


def test_check_core_moves():
    p = _tmp_db()
    with mock.patch("invest.intraday.fetch_current_price", side_effect=lambda s: {"000001": 10.5}.get(s)):
        alerts = check_core_moves(p, threshold=0.03)
    assert len(alerts) == 1
    assert alerts[0]["symbol"] == "000001"
    assert abs(alerts[0]["pct"] - 0.05) < 1e-6
    with mock.patch("invest.intraday.fetch_current_price", side_effect=lambda s: None):
        assert check_core_moves(p, threshold=0.03) == []
    print("test_check_core_moves OK")


def test_send_alerts_mocked():
    p = _tmp_db()
    with mock.patch("invest.notifier.Notifier") as m:
        m.return_value.send_text.return_value = True
        with mock.patch("invest.intraday._attribute", return_value="资金推动"):
            n = send_alerts(p, [{"symbol": "000001", "price": 10.5, "pct": 0.05}])
    assert n == 1
    assert "归因: 资金推动" in m.return_value.send_text.call_args.args[0]
    # 归因失败不影响推送
    with mock.patch("invest.notifier.Notifier") as m2:
        m2.return_value.send_text.return_value = True
        with mock.patch("invest.intraday._attribute", side_effect=Exception("x")):
            n2 = send_alerts(p, [{"symbol": "000001", "price": 10.5, "pct": 0.05}])
    assert n2 == 1
    print("test_send_alerts_mocked OK")


if __name__ == "__main__":
    test_trading_window()
    test_check_core_moves()
    test_send_alerts_mocked()
    print("\nALL INTRADAY TESTS PASSED")