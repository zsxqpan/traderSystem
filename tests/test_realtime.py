"""实时行情三源直连单元测试（纯 mock，不依赖网络）。用法: python tests/test_realtime.py"""
from __future__ import annotations

import datetime as dt
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.data import realtime as rt


def _fake_sina(session, symbols):
    out = {}
    for s in symbols:
        ms = rt._to_market_symbol(s)
        out[ms] = rt.Quote(symbol=ms, price=10.0, pct=0.01, ts=dt.datetime.now(), src="sina")
    return out


def test_symbol_mapping():
    assert rt._to_market_symbol("600519") == "sh600519"
    assert rt._to_market_symbol("000001") == "sz000001"
    assert rt._to_market_symbol("300750") == "sz300750"
    assert rt._to_market_symbol("830001") == "bj830001"
    assert rt._to_market_symbol("sh600000") == "sh600000"
    assert rt._em_secid("600519") == "1.600519"
    assert rt._em_secid("000001") == "0.000001"
    print("test_symbol_mapping OK")


def test_fetch_success_and_order():
    with mock.patch.object(rt, "_fetch_sina", side_effect=_fake_sina), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=AssertionError("不应调用腾讯")):
        with rt.RealtimeQuoter() as q:
            quotes = q.fetch(["600519", "000001"])
    assert len(quotes) == 2
    assert all(v.src == "sina" for v in quotes.values())
    print("test_fetch_success_and_order OK")


def test_failover_to_next_source():
    with mock.patch.object(rt, "_fetch_sina", side_effect=rt.requests.ConnectionError("sina down")), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=_fake_sina):
        with rt.RealtimeQuoter() as q:
            quotes = q.fetch(["600519"])
    assert quotes and list(quotes.values())[0].src == "sina"  # _fake_sina 标记 sina，仅验证已切换
    assert quotes  # 新浪失败后腾讯兜底成功
    print("test_failover_to_next_source OK")


def test_all_sources_down_raises():
    with mock.patch.object(rt, "_fetch_sina", side_effect=RuntimeError("x")), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=RuntimeError("x")), \
         mock.patch.object(rt, "_fetch_em", side_effect=RuntimeError("x")):
        with rt.RealtimeQuoter() as q:
            try:
                q.fetch(["600519"])
                raise AssertionError("应抛 RuntimeError")
            except RuntimeError:
                pass
    print("test_all_sources_down_raises OK")


def test_empty_result_falls_through():
    with mock.patch.object(rt, "_fetch_sina", return_value={}), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=_fake_sina):
        with rt.RealtimeQuoter() as q:
            quotes = q.fetch(["600519"])
    assert len(quotes) == 1  # 新浪空结果不算成功，切腾讯
    print("test_empty_result_falls_through OK")


def test_freshness_guard():
    fresh = rt.Quote(symbol="sh600519", price=10.0, ts=dt.datetime.now(), src="sina")
    stale = rt.Quote(symbol="sh600519", price=10.0, ts=dt.datetime.now() - dt.timedelta(seconds=60), src="sina")
    assert rt.is_fresh(fresh, max_lag=10) is True
    assert rt.is_fresh(stale, max_lag=10) is False
    assert rt.is_fresh(rt.Quote(symbol="x"), max_lag=10) is False  # 无时间戳不新鲜
    assert rt.quote_lag_seconds(fresh) < 1
    assert rt.quote_lag_seconds(rt.Quote(symbol="x")) is None
    print("test_freshness_guard OK")


def test_parse_sina_line():
    line = 'var hq_str_sh600519="贵州茅台,1355.000,1355.290,1341.990,1359.000,1338.140,1341.980,1341.990,2985315,4024065608.000,100,1341.980,200,1341.900,100,1341.690,100,1341.680,300,1341.620,28316,1341.990,2300,1342.000,100,1342.010,200,1342.020,300,1342.060,2026-08-14,15:34:56,00,D|1600|2147184.00";'
    q = rt._parse_sina_line(line)
    assert q is not None
    assert q.symbol == "sh600519"
    assert q.price == 1341.99
    assert abs(q.pct - (1341.99 / 1355.29 - 1)) < 1e-9
    assert q.ts == dt.datetime(2026, 8, 14, 15, 34, 56)
    print("test_parse_sina_line OK")




def _health_db():
    import os as _os
    import tempfile as _tf
    from invest.db import connect, init_db
    p = _os.path.join(_tf.gettempdir(), "invest_realtime_health_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            _os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_realtime_health_states():
    from invest.db import connect
    p = _health_db()
    # 无留痕 -> 不可用
    assert rt.realtime_health(p)["ok"] is False
    # 新鲜留痕 -> 可用
    conn = connect(p)
    with conn:
        conn.execute(
            """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
               VALUES('realtime','ok',datetime('now','localtime'),datetime('now','localtime'),
                      'src=sina n=1 lag_avg=1.0s stale=0 failures={}')"""
        )
    conn.close()
    h = rt.realtime_health(p)
    assert h["ok"] is True and h["stale"] == 0
    # stale>0 -> 不可用
    conn = connect(p)
    with conn:
        conn.execute(
            """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
               VALUES('realtime','ok',datetime('now','localtime'),datetime('now','localtime'),
                      'src=sina n=1 stale=3 failures={}')"""
        )
    conn.close()
    assert rt.realtime_health(p)["ok"] is False
    print("test_realtime_health_states OK")


if __name__ == "__main__":
    test_symbol_mapping()
    test_fetch_success_and_order()
    test_failover_to_next_source()
    test_all_sources_down_raises()
    test_empty_result_falls_through()
    test_freshness_guard()
    test_parse_sina_line()
    test_realtime_health_states()
    print("\nALL REALTIME TESTS PASSED")
