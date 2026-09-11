"""阶段 C：发现宇宙 + 短线变厚。临时库，注入行情/映射，不联网。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.data.storage import upsert_df
from invest.db import connect, init_db

ASOF = dt.date(2026, 8, 22)
YDAY = dt.date(2026, 8, 21)


@pytest.fixture(autouse=True)
def _no_industry_net(monkeypatch):
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_signals_c_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed_bars(conn, symbol: str, *, last_vol: float, avg_vol: float, last_close: float = 10.0,
               amount: float | None = None, n: int = 25):
    high20 = last_close
    rows = []
    for i in range(n):
        d = ASOF - dt.timedelta(days=n - 1 - i)
        close = last_close - (n - 1 - i) * 0.01
        high = high20 if i == n - 1 else close + 0.05
        vol = last_vol if i == n - 1 else avg_vol
        amt = (amount if i == n - 1 and amount is not None else vol * close * 100)
        rows.append({
            "symbol": symbol, "date": d.isoformat(),
            "open": close, "high": high, "low": close - 0.05, "close": close,
            "volume": vol, "amount": amt, "src": "akshare",
        })
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))


def _add_pool(conn, symbol: str, level: str = "core", industry: str = "半导体"):
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES(?,?,?,?)",
        (symbol, level, industry, ASOF.isoformat()),
    )


def _zt(conn, symbol: str, name: str = "", lianban: int = 1, zhaban: int = 0, day=None):
    day = day or YDAY
    conn.execute(
        "INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) VALUES(?,?,?,?,?)",
        (day.strftime("%Y%m%d"), symbol, name or symbol, lianban, zhaban),
    )


def _strength(conn, rows: list[dict], period: str = "short", obj_type: str = "industry"):
    payload = []
    for r in rows:
        payload.append({
            "run_date": r.get("run_date", ASOF.isoformat()),
            "obj_type": r.get("obj_type", obj_type),
            "obj": r["obj"],
            "period": period,
            "rs": r["rs"],
            "momentum": 0.0,
            "trend_stage": r.get("trend_stage", "震荡"),
            "calc_version": "v1",
        })
    upsert_df(conn, "quant_strength", pd.DataFrame(payload))


def test_c1_discovery_cap_truncates_200_zt():
    from invest.signals.thresholds import DISCOVERY_STOCK_CAP
    from invest.signals.universe import discovery_cap_ok, discovery_symbols

    p = _tmp_db()
    conn = connect(p)
    try:
        for i in range(200):
            _zt(conn, f"{i:06d}", lianban=1)
        conn.commit()
        syms = discovery_symbols(conn, ASOF)
        assert len(syms) <= DISCOVERY_STOCK_CAP
        discovery_cap_ok(syms)
        assert len(syms) == DISCOVERY_STOCK_CAP
    finally:
        conn.close()


def test_c1_out_of_pool_not_via_watch_but_via_zt():
    from invest.signals.universe import discovery_symbols, watch_symbols

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        conn.commit()
        assert "000002" not in watch_symbols(conn)
        assert "000002" not in discovery_symbols(conn, ASOF)
        _zt(conn, "000002", "万科")
        conn.commit()
        assert "000002" not in watch_symbols(conn)
        assert "000002" in discovery_symbols(conn, ASOF)
    finally:
        conn.close()


def test_c2_rs_core_top3_skip_unmapped(monkeypatch):
    from invest.signals.universe import rs_core_symbols

    mapping = {f"00001{i}": "半导体" for i in range(5)}
    monkeypatch.setattr("invest.data.industry_map.load_industry_stocks", lambda path=None: mapping)

    p = _tmp_db()
    conn = connect(p)
    try:
        _strength(conn, [
            {"obj": "半导体", "rs": 0.40},
            {"obj": "银行", "rs": 0.30},
        ])
        for i, amt in enumerate([5e8, 4e8, 3e8, 2e8, 1e8]):
            _seed_bars(conn, f"00001{i}", last_vol=1000, avg_vol=1000, amount=amt)
        _seed_bars(conn, "601398", last_vol=1000, avg_vol=1000, amount=9e8)
        conn.execute(
            "INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES(?,?,?,?)",
            ("601398", "observe", "银行", ASOF.isoformat()),
        )
        conn.commit()
        cores = rs_core_symbols(conn, ASOF, mapping=mapping)
        assert cores == ["000010", "000011", "000012"]
        assert "601398" not in cores
        assert "000013" not in cores
    finally:
        conn.close()


def test_c3_shrink_discovery_not_in_pool():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _zt(conn, "000002", "万科")
        _seed_bars(conn, "000002", last_vol=10_000, avg_vol=10_000, last_close=10.0)
        conn.commit()
        now = dt.datetime(2026, 8, 22, 14, 0)
        quotes = {"000002": {"name": "万科", "price": 9.90, "pct": -0.9, "vol": 2250}}
        sigs = scan(conn, "intraday", asof=ASOF, now=now, quotes=quotes, persist=False)
        hit = [s for s in sigs if s.id == "shrink_extreme" and s.subject == "000002"]
        assert hit, [s.id for s in sigs]
        assert hit[0].layer == "discovery"
        assert hit[0].severity == "action"
    finally:
        conn.close()


def test_c3_scan_does_not_walk_5000_daily_bars(monkeypatch):
    import importlib

    from invest.signals.thresholds import DISCOVERY_STOCK_CAP
    from invest.signals.universe import discovery_symbols

    scan_mod = importlib.import_module("invest.signals.scan")
    p = _tmp_db()
    conn = connect(p)
    try:
        _zt(conn, "000002")
        rows = []
        for i in range(5000):
            rows.append({
                "symbol": f"{i:06d}", "date": YDAY.isoformat(),
                "open": 10, "high": 10, "low": 10, "close": 10,
                "volume": 1, "amount": 1, "src": "akshare",
            })
        upsert_df(conn, "daily_bars", pd.DataFrame(rows))
        conn.commit()
        disc = discovery_symbols(conn, ASOF)
        assert len(disc) <= DISCOVERY_STOCK_CAP

        captured: list[str] = []
        orig = scan_mod.shrink_highvol_signals

        def _wrap(session, now, watch, quotes, stats, lianban):
            captured.extend(watch)
            return orig(session, now, watch, quotes, stats, lianban)

        monkeypatch.setattr(scan_mod, "shrink_highvol_signals", _wrap)
        now = dt.datetime(2026, 8, 22, 14, 0)
        scan_mod.scan(conn, "intraday", asof=ASOF, now=now, quotes={})
        assert captured
        assert len(captured) <= DISCOVERY_STOCK_CAP
        assert len(set(captured)) <= DISCOVERY_STOCK_CAP
    finally:
        conn.close()


def test_c4_dragon_tiger_enters_discovery():
    from invest.signals.universe import discovery_symbols

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute(
            "INSERT INTO dragon_tiger(date, symbol, name, seat_type, buy, sell, net) "
            "VALUES(?,?,?,?,?,?,?)",
            (YDAY.isoformat(), "000002", "万科", "机构", 1e8, 0, 1e8),
        )
        conn.commit()
        assert "000002" in discovery_symbols(conn, ASOF)
        conn.execute("DELETE FROM dragon_tiger")
        conn.commit()
        assert "000002" not in discovery_symbols(conn, ASOF)
    finally:
        conn.close()


def test_c4_dragon_tiger_cap_and_empty_ok():
    from invest.signals.universe import discovery_symbols, lhb_symbols

    p = _tmp_db()
    conn = connect(p)
    try:
        assert lhb_symbols(conn) == []
        for i in range(50):
            conn.execute(
                "INSERT INTO dragon_tiger(date, symbol, name, seat_type, buy, sell, net) "
                "VALUES(?,?,?,?,?,?,?)",
                (YDAY.isoformat(), f"{i:06d}", f"N{i}", "机构", 1.0, 0, 1.0),
            )
        conn.commit()
        lhb = lhb_symbols(conn)
        assert len(lhb) <= 30
        disc = discovery_symbols(conn, ASOF)
        assert len([s for s in disc if s in set(lhb)]) <= 30
    finally:
        conn.close()


def test_c5_auction_keep_vol_yoy_with_and_without_snapshot():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _seed_bars(conn, "600519", last_vol=10_000, avg_vol=10_000)
        conn.commit()
        quotes = {"600519": {"name": "茅台", "price": 10.0, "pct": 0.5, "vol": 90}}
        sigs = scan(conn, "auction", asof=ASOF, quotes=quotes)
        assert not any(s.id == "auction_keep_vol_yoy" for s in sigs)

        conn.execute(
            "INSERT INTO auction_snapshots(date, symbol, name, price, pct, vol, amount) "
            "VALUES(?,?,?,?,?,?,?)",
            (YDAY.isoformat(), "600519", "茅台", 10.0, 1.0, 100.0, 1e5),
        )
        conn.commit()
        sigs2 = scan(conn, "auction", asof=ASOF, quotes=quotes)
        yoy = [s for s in sigs2 if s.id == "auction_keep_vol_yoy" and s.subject == "600519"]
        assert yoy and yoy[0].evidence.get("ratio", 0) >= 0.8
    finally:
        conn.close()


def test_c6_board_high_low_from_injected_boards(monkeypatch):
    from invest.signals.scan import scan

    def _boom(*_a, **_k):
        raise AssertionError("boards=None must not hit eastmoney")

    monkeypatch.setattr("invest.data.auction.fetch_top_gainers", _boom)
    monkeypatch.setattr("invest.data.auction.fetch_top_losers", _boom)
    monkeypatch.setattr("invest.data.auction.fetch_vol_top", _boom)

    p = _tmp_db()
    conn = connect(p)
    try:
        quotes = {}
        none_sigs = scan(conn, "auction", asof=ASOF, quotes=quotes, boards=None)
        assert not any(s.id.startswith("board_") for s in none_sigs)

        boards = [
            {"symbol": "000001", "name": "平安", "pct": 4.2, "vol": 8000, "amount": 25_000_000},
            {"symbol": "000002", "name": "万科", "pct": -3.5, "vol": 7000, "amount": 22_000_000},
        ]
        sigs = scan(conn, "auction", asof=ASOF, quotes=quotes, boards=boards)
        high = next(s for s in sigs if s.id == "board_high_open_vol")
        low = next(s for s in sigs if s.id == "board_low_open_vol")
        assert high.subject == "000001" and high.layer == "discovery" and high.severity == "watch"
        assert low.subject == "000002"
        assert low.severity in ("info", "watch")
    finally:
        conn.close()


def test_c6_fetch_top_gainers_includes_vol_amount(monkeypatch):
    from invest.data import auction

    monkeypatch.setattr(auction, "_em_get", lambda url: {
        "data": {"diff": [{"f12": "600519", "f14": "茅台", "f2": 10.0, "f3": 3.1,
                            "f5": 12345, "f6": 25000000}]},
    })
    rows = auction.fetch_top_gainers(1)
    assert rows[0]["vol"] == 12345
    assert rows[0]["amount"] == 25000000


def test_c7_sector_flow_spike_daily_only():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        rows = []
        for i in range(5):
            d = (ASOF - dt.timedelta(days=5 - i)).isoformat()
            rows.append({"date": d, "industry": "半导体", "main_net": 1e8, "src": "eastmoney"})
        rows.append({"date": ASOF.isoformat(), "industry": "半导体", "main_net": 3e8, "src": "eastmoney"})
        rows.append({"date": ASOF.isoformat(), "industry": "银行", "main_net": -3e8, "src": "eastmoney"})
        upsert_df(conn, "sector_fund_flow", pd.DataFrame(rows))
        conn.commit()
        daily = scan(conn, "daily", asof=ASOF)
        hit = [s for s in daily if s.id == "sector_flow_spike" and s.subject == "半导体"]
        assert hit and hit[0].horizon == "short" and hit[0].session == "daily"
        assert hit[0].subject_type == "sector"
        assert not any(s.id == "sector_flow_spike" and s.subject == "银行" for s in daily)
        close = scan(conn, "close", asof=ASOF, quotes={})
        assert not any(s.id == "sector_flow_spike" for s in close)
    finally:
        conn.close()


def test_c8_lianban_promote_and_fail():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _zt(conn, "000001", "平安", lianban=2, zhaban=0, day=YDAY)
        _zt(conn, "000001", "平安", lianban=3, zhaban=0, day=ASOF)
        _zt(conn, "000002", "万科", lianban=3, zhaban=0, day=YDAY)
        _zt(conn, "000002", "万科", lianban=3, zhaban=1, day=ASOF)
        conn.commit()
        sigs = scan(conn, "close", asof=ASOF, quotes={})
        promo = next(s for s in sigs if s.id == "lianban_promote" and s.subject == "000001")
        assert promo.severity == "action" and promo.layer == "discovery"
        fail = next(s for s in sigs if s.id == "lianban_fail" and s.subject == "000002")
        assert fail.severity == "watch"
        assert not any(s.id == "lianban_promote" and s.subject == "000002" for s in sigs)
    finally:
        conn.close()


def test_c9_rs_industry_leader_rank_up():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        yday_rows = []
        today_rows = []
        for i in range(12):
            name = "半导体" if i == 11 else f"行业{i:02d}"
            yday_rows.append({"run_date": YDAY.isoformat(), "obj": name, "rs": 12 - i})
        for i, name in enumerate(
            [f"行业{i:02d}" for i in range(4)] + ["半导体"] + [f"行业{i:02d}" for i in range(4, 11)]
        ):
            today_rows.append({"run_date": ASOF.isoformat(), "obj": name, "rs": 20 - i})
        _strength(conn, yday_rows + today_rows)
        conn.commit()
        sigs = scan(conn, "daily", asof=ASOF)
        hit = [s for s in sigs if s.id == "rs_industry_leader" and s.subject == "半导体"]
        assert hit and hit[0].horizon == "short" and hit[0].session == "daily"
        assert hit[0].subject_type == "sector"
        flat = [s for s in sigs if s.id == "rs_industry_leader" and s.subject == "行业00"]
        assert not flat
    finally:
        conn.close()


def test_c10_fetch_quotes_covers_discovery(monkeypatch):
    import importlib

    from invest.signals.thresholds import DISCOVERY_STOCK_CAP
    from invest.signals.universe import quote_symbols, watch_symbols

    scan_mod = importlib.import_module("invest.signals.scan")
    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _zt(conn, "000002", "万科")
        _seed_bars(conn, "600519", last_vol=10_000, avg_vol=10_000)
        _seed_bars(conn, "000002", last_vol=10_000, avg_vol=10_000)
        conn.commit()
        uni = quote_symbols(conn, ASOF)
        assert "000002" in uni and "600519" in uni
        assert len(uni) <= DISCOVERY_STOCK_CAP + len(watch_symbols(conn))

        seen: list[str] = []

        def _fake(symbols):
            seen.extend(symbols)
            return {s: {"name": s, "price": 10.0, "pct": 0.0, "vol": 100} for s in symbols}

        monkeypatch.setattr(scan_mod, "_fetch_quotes", _fake)
        now = dt.datetime(2026, 8, 22, 14, 0)
        scan_mod.scan(conn, "intraday", asof=ASOF, now=now, quotes=None)
        assert "000002" in seen
        assert len(seen) <= DISCOVERY_STOCK_CAP + 1
    finally:
        conn.close()
