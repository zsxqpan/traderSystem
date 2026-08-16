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


def _fake_quoter(price_map: dict[str, float]):
    """构造 FakeQuoter：fetch 返回新鲜 Quote 字典，带 source_failures 属性。"""
    from invest.data.realtime import Quote

    class FakeQuoter:
        source_failures = {"sina": 0, "tencent": 0, "em_push2": 0}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def fetch(self, symbols):
            return {
                f"sz{s}": Quote(symbol=f"sz{s}", price=p, ts=dt.datetime.now(), src="sina")
                for s, p in price_map.items()
            }

    return FakeQuoter


def test_trading_window():
    assert _in_trading_window(dt.datetime(2026, 8, 3, 10, 0)) is True
    assert _in_trading_window(dt.datetime(2026, 8, 3, 9, 20)) is False
    assert _in_trading_window(dt.datetime(2026, 8, 3, 11, 40)) is False
    assert _in_trading_window(dt.datetime(2026, 8, 3, 13, 30)) is True
    assert _in_trading_window(dt.datetime(2026, 8, 8, 10, 0)) is False  # 周六
    print("test_trading_window OK")


def test_check_core_moves():
    p = _tmp_db()
    # 000001 现价 10.5（+5% 触发），600000 无报价跳过
    with mock.patch("invest.data.realtime.RealtimeQuoter", _fake_quoter({"000001": 10.5})):
        alerts = check_core_moves(p, threshold=0.03)
    assert len(alerts) == 1
    assert alerts[0]["symbol"] == "000001"
    assert abs(alerts[0]["pct"] - 0.05) < 1e-6
    # 无报价 -> 无异动
    with mock.patch("invest.data.realtime.RealtimeQuoter", _fake_quoter({})):
        assert check_core_moves(p, threshold=0.03) == []
    # 三源全挂（异常）-> 无异动，不抛，留痕 job_runs
    with mock.patch("invest.data.realtime.RealtimeQuoter", side_effect=RuntimeError("all down")):
        assert check_core_moves(p, threshold=0.03) == []
    print("test_check_core_moves OK")


def test_send_alerts_mocked():
    p = _tmp_db()
    # 交易时段内：000001 为 core -> P0 立即推，附归因
    with mock.patch("invest.intraday._in_trading_window", return_value=True),          mock.patch("invest.notifier.Notifier") as m:
        m.return_value.send_text.return_value = True
        with mock.patch("invest.intraday._attribute", return_value="资金推动"):
            n = send_alerts(p, [{"symbol": "000001", "price": 10.5, "pct": 0.05}])
    assert n == 1
    assert "[P0]" in m.return_value.send_text.call_args.args[0]
    assert "归因: 资金推动" in m.return_value.send_text.call_args.args[0]
    # 归因失败不影响推送
    with mock.patch("invest.intraday._in_trading_window", return_value=True),          mock.patch("invest.notifier.Notifier") as m2:
        m2.return_value.send_text.return_value = True
        with mock.patch("invest.intraday._attribute", side_effect=Exception("x")):
            n2 = send_alerts(p, [{"symbol": "000001", "price": 10.5, "pct": 0.05}])
    assert n2 == 1
    print("test_send_alerts_mocked OK")


def test_send_alerts_off_hours_silent():
    p = _tmp_db()
    # 非交易时段（周六/盘后）：一律静默，不调 Notifier
    with mock.patch("invest.intraday._in_trading_window", return_value=False),          mock.patch("invest.notifier.Notifier") as m:
        n = send_alerts(p, [{"symbol": "000001", "price": 10.5, "pct": 0.05}])
    assert n == 0
    m.return_value.send_text.assert_not_called()
    print("test_send_alerts_off_hours_silent OK")


def test_send_alerts_priority_filter():
    p = _tmp_db()
    # 600000 为 track -> P1 降频；rest 级（无记录）不推
    alerts = [
        {"symbol": "000001", "price": 10.5, "pct": 0.05},   # core -> P0
        {"symbol": "600000", "price": 21.0, "pct": 0.05},   # track -> P1
        {"symbol": "999999", "price": 5.0, "pct": 0.05},    # 不在池 -> 不推
    ]
    with mock.patch("invest.intraday._in_trading_window", return_value=True),          mock.patch("invest.notifier.Notifier") as m:
        m.return_value.send_text.return_value = True
        n = send_alerts(p, alerts, attribute=False)
    assert n == 2
    keys = [c.args[0] for c in m.return_value.send_text.call_args_list]
    assert any(k.startswith("[P0]") for k in keys)
    assert any(k.startswith("[P1]") for k in keys)
    assert not any("999999" in k for k in keys)
    print("test_send_alerts_priority_filter OK")


if __name__ == "__main__":
    test_trading_window()
    test_check_core_moves()
    test_send_alerts_mocked()
    test_send_alerts_off_hours_silent()
    test_send_alerts_priority_filter()
    print("\nALL INTRADAY TESTS PASSED")
