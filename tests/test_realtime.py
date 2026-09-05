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
         mock.patch.object(rt, "_fetch_tencent", side_effect=AssertionError("不应调用腾讯")), rt.RealtimeQuoter() as q:
        quotes = q.fetch(["600519", "000001"])
    assert len(quotes) == 2
    assert all(v.src == "sina" for v in quotes.values())
    print("test_fetch_success_and_order OK")


def test_failover_to_next_source():
    with mock.patch.object(rt, "_fetch_sina", side_effect=rt.requests.ConnectionError("sina down")), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=_fake_sina), rt.RealtimeQuoter() as q:
        quotes = q.fetch(["600519"])
    assert quotes and next(iter(quotes.values())).src == "sina"  # _fake_sina 标记 sina，仅验证已切换
    assert quotes  # 新浪失败后腾讯兜底成功
    print("test_failover_to_next_source OK")


def test_all_sources_down_raises():
    with mock.patch.object(rt, "_fetch_sina", side_effect=RuntimeError("x")), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=RuntimeError("x")), \
         mock.patch.object(rt, "_fetch_em", side_effect=RuntimeError("x")), rt.RealtimeQuoter() as q:
        try:
            q.fetch(["600519"])
            raise AssertionError("应抛 RuntimeError")
        except RuntimeError:
            pass
    print("test_all_sources_down_raises OK")


def test_empty_result_falls_through():
    with mock.patch.object(rt, "_fetch_sina", return_value={}), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=_fake_sina), rt.RealtimeQuoter() as q:
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


def test_partial_merge_fills_missing_from_next_source():
    """核心 bug：新浪部分成功后必须继续对 missing/stale 标的请求腾讯/东财。"""
    now = dt.datetime.now()
    tencent_called = []

    def _sina(session, symbols):
        return {"sh600519": rt.Quote(symbol="sh600519", price=100.0, pct=0.01, ts=now, src="sina")}

    def _tencent(session, symbols):
        tencent_called.append(list(symbols))
        out = {}
        for s in symbols:
            bare = s[2:] if s[:2] in ("sh", "sz", "bj") else s
            if bare == "000001":
                out["sz000001"] = rt.Quote(
                    symbol="sz000001", price=11.0, pct=0.02, ts=now, src="tencent"
                )
        return out

    with mock.patch.object(rt, "_fetch_sina", side_effect=_sina), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=_tencent), \
         mock.patch.object(rt, "_fetch_em", return_value={}), rt.RealtimeQuoter() as q:
        quotes = q.fetch(["600519", "000001"])
    assert tencent_called, "新浪部分成功后应继续请求腾讯"
    asked = [s[2:] if s[:2] in ("sh", "sz", "bj") else s for s in tencent_called[0]]
    assert "000001" in asked
    assert "600519" not in asked  # 已 live 的不再打下一源
    bares = {s[2:] if s[:2] in ("sh", "sz", "bj") else s for s in quotes}
    assert "600519" in bares and "000001" in bares
    srcs = { (k[2:] if k[:2] in ("sh", "sz", "bj") else k): v.src for k, v in quotes.items() }
    assert srcs["600519"] == "sina"
    assert srcs["000001"] == "tencent"
    print("test_partial_merge_fills_missing_from_next_source OK")


def test_stale_symbol_continues_to_next_source():
    """逐标的新鲜度：stale 标的继续换源，live 保留。"""
    now = dt.datetime.now()
    stale_ts = now - dt.timedelta(seconds=120)
    tencent_called = []

    def _sina(session, symbols):
        return {
            "sh600519": rt.Quote(symbol="sh600519", price=100.0, pct=0.01, ts=now, src="sina"),
            "sz000001": rt.Quote(symbol="sz000001", price=9.0, pct=0.0, ts=stale_ts, src="sina"),
        }

    def _tencent(session, symbols):
        tencent_called.append(list(symbols))
        return {
            "sz000001": rt.Quote(symbol="sz000001", price=11.0, pct=0.02, ts=now, src="tencent"),
        }

    with mock.patch.object(rt, "_fetch_sina", side_effect=_sina), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=_tencent), \
         mock.patch.object(rt, "_fetch_em", return_value={}), rt.RealtimeQuoter() as q:
        quotes = q.fetch(["600519", "000001"])
    assert tencent_called
    asked = [s[2:] if s[:2] in ("sh", "sz", "bj") else s for s in tencent_called[0]]
    assert asked == ["000001"]
    by_bare = { (k[2:] if k[:2] in ("sh", "sz", "bj") else k): v for k, v in quotes.items() }
    assert by_bare["600519"].src == "sina"
    assert by_bare["000001"].src == "tencent"
    assert by_bare["000001"].price == 11.0
    print("test_stale_symbol_continues_to_next_source OK")


def test_zero_price_with_name_tries_next_source():
    """源层价=0 但有名称：不是停牌终态；下一源有价则换源。"""
    now = dt.datetime.now()
    tencent_called = []

    def _sina(session, symbols):
        return {
            "sh600519": rt.Quote(
                symbol="sh600519", price=0.0, pct=None, ts=now, src="sina",
                prev_close=100.0, name="贵州茅台", suspended=True,
            ),
        }

    def _tencent(session, symbols):
        tencent_called.append(list(symbols))
        return {
            "sh600519": rt.Quote(
                symbol="sh600519", price=101.0, pct=0.01, ts=now, src="tencent",
                prev_close=100.0, name="贵州茅台",
            ),
        }

    with mock.patch.object(rt, "_fetch_sina", side_effect=_sina), \
         mock.patch.object(rt, "_fetch_tencent", side_effect=_tencent), \
         mock.patch.object(rt, "_fetch_em", return_value={}), rt.RealtimeQuoter() as q:
        quotes = q.fetch(["600519"])
        resolved = q.fetch_resolved(["600519"])
    assert tencent_called, "价=0 有名称也应继续问下一源"
    by_bare = {(k[2:] if k[:2] in ("sh", "sz", "bj") else k): v for k, v in quotes.items()}
    assert by_bare["600519"].src == "tencent"
    assert by_bare["600519"].price == 101.0
    assert resolved["600519"].status == "live"
    assert resolved["600519"].price == 101.0
    assert resolved["600519"].fallback_level != "suspended"
    print("test_zero_price_with_name_tries_next_source OK")


def test_fetch_keeps_requested_when_later_sources_empty():
    """后续源为空时，已成功标的保留；缺失标的不能从结果里被静默丢掉（由 fetch_resolved 补齐）。"""
    now = dt.datetime.now()

    def _sina(session, symbols):
        return {"sh600519": rt.Quote(symbol="sh600519", price=100.0, pct=0.01, ts=now, src="sina")}

    with mock.patch.object(rt, "_fetch_sina", side_effect=_sina), \
         mock.patch.object(rt, "_fetch_tencent", return_value={}), \
         mock.patch.object(rt, "_fetch_em", return_value={}), rt.RealtimeQuoter() as q:
        quotes = q.fetch(["600519", "000001"])
        resolved = q.fetch_resolved(["600519", "000001"])
    assert any((k[2:] if k[:2] in ("sh", "sz", "bj") else k) == "600519" for k in quotes)
    assert set(resolved) == {"600519", "000001"}
    assert resolved["600519"].status == "live"
    assert resolved["000001"].status == "missing"
    print("test_fetch_keeps_requested_when_later_sources_empty OK")


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

    from invest.db import init_db
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
    test_partial_merge_fills_missing_from_next_source()
    test_stale_symbol_continues_to_next_source()
    test_zero_price_with_name_tries_next_source()
    test_fetch_keeps_requested_when_later_sources_empty()
    test_parse_sina_line()
    test_realtime_health_states()
    print("\nALL REALTIME TESTS PASSED")
