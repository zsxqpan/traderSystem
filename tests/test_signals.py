"""短线交易信号引擎（invest/signals）：纯规则、临时库、注入行情，不连真实网络。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import invest.skills  # noqa: F401  注册 d32
from invest.data.storage import upsert_df
from invest.db import connect, init_db, table_names

ASOF = dt.date(2026, 8, 22)
YDAY = dt.date(2026, 8, 21)


@pytest.fixture(autouse=True)
def _no_industry_net(monkeypatch):
    """热门板块核心会调 fetch_industries；单测默认空映射，不联网。"""
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_signals_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed_bars(conn, symbol: str, *, last_vol: float, avg_vol: float, last_close: float = 10.0,
               high20: float | None = None, n: int = 25):
    """造 n 日日线：前 n-1 日均量 avg_vol、收盘略低于 last；末日量 last_vol、收盘 last_close。"""
    high20 = last_close if high20 is None else high20
    rows = []
    for i in range(n):
        d = ASOF - dt.timedelta(days=n - 1 - i)
        close = last_close - (n - 1 - i) * 0.01
        high = high20 if i == n - 1 else close + 0.05
        vol = last_vol if i == n - 1 else avg_vol
        rows.append({
            "symbol": symbol, "date": d.isoformat(),
            "open": close, "high": high, "low": close - 0.05, "close": close,
            "volume": vol, "amount": vol * close * 100, "src": "akshare",
        })
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))


def _add_pool(conn, symbol: str, level: str = "core"):
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES(?,?,?,?)",
        (symbol, level, "半导体", ASOF.isoformat()),
    )


def test_schema_has_signal_tables():
    p = _tmp_db()
    names = table_names(p)
    assert "trade_signals" in names
    assert "auction_snapshots" in names


def test_schema_has_horizon_layer_columns():
    p = _tmp_db()
    conn = connect(p)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_signals)")}
        assert "horizon" in cols
        assert "layer" in cols
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver >= 13
    finally:
        conn.close()


def test_migrate_adds_horizon_layer_to_old_table():
    """SCHEMA 12 旧表无新列：_migrate 幂等加列。"""
    from invest.db import _migrate

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute("DROP TABLE trade_signals")
        conn.execute(
            """CREATE TABLE trade_signals (
                date TEXT NOT NULL,
                session TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                subject_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                severity TEXT NOT NULL,
                name TEXT,
                hint TEXT,
                evidence TEXT,
                src TEXT NOT NULL DEFAULT 'signals',
                PRIMARY KEY (date, session, signal_id, subject)
            )"""
        )
        conn.commit()
        cols_before = {r[1] for r in conn.execute("PRAGMA table_info(trade_signals)")}
        assert "horizon" not in cols_before
        _migrate(conn)
        _migrate(conn)  # 幂等
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_signals)")}
        assert "horizon" in cols
        assert "layer" in cols
    finally:
        conn.close()


def test_init_db_twice_idempotent():
    p = _tmp_db()
    init_db(p)
    conn = connect(p)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_signals)")}
        assert "horizon" in cols and "layer" in cols
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver >= 13
    finally:
        conn.close()


def test_signal_defaults_horizon_short_layer_watch():
    from invest.signals.types import HORIZONS, LAYERS, SESSIONS, Signal

    s = Signal(
        id="x", name="n", session="intraday", severity="info",
        subject_type="stock", subject="600519", hint="h",
    )
    assert s.horizon == "short"
    assert s.layer == "watch"
    assert "daily" in SESSIONS
    assert HORIZONS == ("short", "mid")
    assert LAYERS == ("watch", "discovery", "market")


def test_assign_layer_market_watch_discovery():
    from invest.signals.universe import assign_layer

    watch = {"600519"}
    discovery = {"000001"}
    assert assign_layer("market", "市场", watch, discovery) == "market"
    assert assign_layer("etf", "510300", watch, discovery) == "market"
    assert assign_layer("sector", "半导体", watch, discovery) == "discovery"
    assert assign_layer("stock", "600519", watch, discovery) == "watch"
    assert assign_layer("stock", "000002", watch, discovery) == "discovery"


def test_thresholds_phase2_constants():
    from invest.signals.thresholds import (
        AUCTION_YOY,
        BOARD_HIGH_OPEN_PCT,
        BOARD_LOW_OPEN_PCT,
        BOARD_TOP_N,
        DISCOVERY_STOCK_CAP,
        DISPLAY_A3,
        DISPLAY_A7,
        DISPLAY_B1_DISCOVERY_ACTION,
        DISPLAY_B1_WATCH,
        DISPLAY_LIMIT,
        DISPLAY_WIDE,
        FLOW_SPIKE,
        PATH_DAYS,
        QUAD_CHASE_LIMIT,
        QUAD_CROWDING,
        QUAD_HUNT_LIMIT,
        QUAD_RS_ZERO,
        QUAD_WATCH_LIMIT,
        ROTATION_LEAD_RANK,
        RS_CORE_PER_INDUSTRY,
        RS_INDUSTRY_TOP,
        RS_LEADER_RANK,
        RS_LEADER_UP,
    )

    assert DISPLAY_LIMIT == 8
    assert DISPLAY_B1_WATCH == 12
    assert DISPLAY_B1_DISCOVERY_ACTION == 5
    assert DISPLAY_A7 == 12
    assert DISPLAY_A3 == 12
    assert DISPLAY_WIDE == 30
    assert DISCOVERY_STOCK_CAP == 80
    assert RS_INDUSTRY_TOP == 8
    assert RS_CORE_PER_INDUSTRY == 3
    assert BOARD_TOP_N == 10
    assert AUCTION_YOY == 0.80
    assert BOARD_HIGH_OPEN_PCT == 3.0
    assert BOARD_LOW_OPEN_PCT == -3.0
    assert FLOW_SPIKE == 2.0
    assert RS_LEADER_RANK == 8
    assert RS_LEADER_UP == 3
    assert ROTATION_LEAD_RANK == 15
    assert QUAD_RS_ZERO == 0.0
    assert QUAD_CROWDING == 0.8
    assert QUAD_HUNT_LIMIT == 8
    assert QUAD_CHASE_LIMIT == 8
    assert QUAD_WATCH_LIMIT == 6
    assert PATH_DAYS == 5


def test_watch_universe_core_track_and_cards():
    from invest.signals.universe import watch_symbols

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519", "core")
        _add_pool(conn, "000001", "track")
        conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date, out_date) "
                     "VALUES('600000','rest','银行',?,?)", (ASOF.isoformat(), ASOF.isoformat()))
        conn.execute("""INSERT INTO cards(symbol, level, cycle, thesis, status, stop_loss, target, created_at)
                        VALUES('002415','A','short','这是一个足够长的投资逻辑说明文本内容','locked',
                               10.0, 20.0, datetime('now','localtime'))""")
        conn.commit()
        syms = watch_symbols(conn)
        assert "600519" in syms and "000001" in syms and "002415" in syms
        assert "600000" not in syms  # 已出池
    finally:
        conn.close()


def test_auction_keep_vol_hits_limit_up_stock():
    """连板/昨涨停：竞价量/昨量 ≥3% → 保量。"""
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _seed_bars(conn, "600519", last_vol=10_000, avg_vol=10_000)
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES(?,?,?,?,0)", (YDAY.strftime("%Y%m%d"), "600519", "茅台", 2))
        conn.commit()
        quotes = {"600519": {"name": "茅台", "price": 10.0, "pct": 1.2, "vol": 400}}  # 4%
        sigs = scan(conn, "auction", asof=ASOF, quotes=quotes)
        ids = [s.id for s in sigs if s.subject == "600519"]
        assert "auction_keep_vol" in ids
    finally:
        conn.close()


def test_scan_daily_does_not_fetch_quotes(monkeypatch):
    import importlib

    scan_mod = importlib.import_module("invest.signals.scan")

    def _boom(_symbols=None):
        raise AssertionError("daily must not fetch quotes")

    monkeypatch.setattr(scan_mod, "_fetch_quotes", _boom)
    p = _tmp_db()
    conn = connect(p)
    try:
        sigs = scan_mod.scan(conn, "daily", asof=ASOF)
        assert sigs == []
    finally:
        conn.close()


def test_scan_layers_filter():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _seed_bars(conn, "600519", last_vol=10_000, avg_vol=10_000)
        _seed_bars(conn, "000002", last_vol=10_000, avg_vol=10_000)
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES(?,?,?,?,0)", (YDAY.strftime("%Y%m%d"), "600519", "茅台", 2))
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES(?,?,?,?,0)", (YDAY.strftime("%Y%m%d"), "000002", "万科", 1))
        conn.commit()
        quotes = {
            "600519": {"name": "茅台", "price": 10.0, "pct": 1.2, "vol": 400},
            "000002": {"name": "万科", "price": 10.0, "pct": 1.2, "vol": 400},
        }
        all_sigs = scan(conn, "auction", asof=ASOF, quotes=quotes, boards=[])
        disc = next(s for s in all_sigs if s.subject == "000002" and s.id == "auction_keep_vol")
        assert disc.layer == "discovery"
        watch = next(s for s in all_sigs if s.subject == "600519" and s.id == "auction_keep_vol")
        assert watch.layer == "watch"
        filtered = scan(conn, "auction", asof=ASOF, quotes=quotes, layers=["watch"])
        subjects = {s.subject for s in filtered if s.id == "auction_keep_vol"}
        assert "600519" in subjects
        assert "000002" not in subjects
        assert scan(conn, "auction", asof=ASOF, quotes=quotes, horizon="mid") == []
    finally:
        conn.close()


def test_auction_shrink_diverge_high_open_no_volume():
    """昨涨停高开但竞价量极低 → 缩量分歧，且不出保量。"""
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _seed_bars(conn, "600519", last_vol=10_000, avg_vol=10_000)
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES(?,?,?,?,0)", (YDAY.strftime("%Y%m%d"), "600519", "茅台", 3))
        conn.commit()
        quotes = {"600519": {"name": "茅台", "price": 10.5, "pct": 2.0, "vol": 50}}  # 0.5%
        sigs = scan(conn, "auction", asof=ASOF, quotes=quotes)
        ids = [s.id for s in sigs if s.subject == "600519"]
        assert "auction_shrink_diverge" in ids
        assert "auction_keep_vol" not in ids
    finally:
        conn.close()


def test_shrink_extreme_time_adjusted_after_open():
    """午盘后时间修正量比 ≤0.4 且不破昨收 → 极致缩量（洗盘观察）。"""
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _seed_bars(conn, "600519", last_vol=10_000, avg_vol=10_000, last_close=10.0)
        conn.commit()
        now = dt.datetime(2026, 8, 22, 14, 0)
        quotes = {"600519": {"name": "茅台", "price": 10.02, "pct": 0.2, "vol": 2000}}
        sigs = scan(conn, "intraday", asof=ASOF, now=now, quotes=quotes)
        hit = [s for s in sigs if s.id == "shrink_extreme" and s.subject == "600519"]
        assert hit and "洗盘" in hit[0].hint
    finally:
        conn.close()


def test_shrink_skipped_before_935():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _seed_bars(conn, "600519", last_vol=10_000, avg_vol=10_000)
        conn.commit()
        now = dt.datetime(2026, 8, 22, 9, 32)
        quotes = {"600519": {"name": "茅台", "price": 10.0, "pct": 0.0, "vol": 10}}
        sigs = scan(conn, "intraday", asof=ASOF, now=now, quotes=quotes)
        assert not any(s.id == "shrink_extreme" for s in sigs)
    finally:
        conn.close()


def test_high_vol_at_high_split_up_vs_stall():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _add_pool(conn, "000001", "track")
        _seed_bars(conn, "600519", last_vol=25_000, avg_vol=10_000, last_close=10.0, high20=10.0)
        _seed_bars(conn, "000001", last_vol=25_000, avg_vol=10_000, last_close=10.0, high20=10.0)
        conn.commit()
        now = dt.datetime(2026, 8, 22, 14, 0)
        quotes = {
            "600519": {"name": "茅台", "price": 10.3, "pct": 3.0, "vol": 20_000},
            "000001": {"name": "平安", "price": 10.0, "pct": 0.1, "vol": 20_000},
        }
        sigs = scan(conn, "intraday", asof=ASOF, now=now, quotes=quotes)
        up = next(s for s in sigs if s.id == "high_vol" and s.subject == "600519")
        stall = next(s for s in sigs if s.id == "high_vol" and s.subject == "000001")
        assert "上涨" in up.hint
        assert "滞涨" in stall.hint or "回落" in stall.hint
    finally:
        conn.close()


def test_sector_collective_needs_three_cores(monkeypatch):
    from invest.signals.scan import scan
    from invest.signals.universe import hot_sector_cores

    p = _tmp_db()
    conn = connect(p)
    try:
        for i, sym in enumerate(("600001", "600002", "600003")):
            _add_pool(conn, sym, "track")
            _seed_bars(conn, sym, last_vol=25_000, avg_vol=10_000, last_close=10.0, high20=10.0)
            conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                         "VALUES(?,?,?,?,0)", (YDAY.strftime("%Y%m%d"), sym, f"股{i}", 1))
        conn.commit()
        now = dt.datetime(2026, 8, 22, 14, 0)
        quotes = {s: {"name": s, "price": 10.2, "pct": 2.0, "vol": 20_000}
                  for s in ("600001", "600002", "600003")}
        monkeypatch.setattr("invest.data.auction.fetch_industries",
                            lambda symbols: {s: "半导体" for s in (symbols or [])})
        cores = hot_sector_cores(conn, asof=ASOF)
        assert cores and sum(len(b["stocks"]) for b in cores) >= 3
        sigs = scan(conn, "intraday", asof=ASOF, now=now, quotes=quotes)
        coll = [s for s in sigs if s.id == "sector_collective"]
        assert coll and coll[0].subject_type == "sector"
        assert coll[0].severity in ("watch", "action")

        conn.execute("DELETE FROM limit_up_pool WHERE symbol='600003'")
        conn.execute("DELETE FROM candidate_pool WHERE symbol='600003'")
        conn.commit()
        sigs2 = scan(conn, "intraday", asof=ASOF, now=now, quotes=quotes)
        assert not any(s.id == "sector_collective" for s in sigs2)
    finally:
        conn.close()


def test_space_height_and_breadth():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        # 昨最高 5 板，今最高 4 板 + 晋级失败；涨停家数较 3 日均大幅收缩
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES(?,?,?,?,0)", (YDAY.strftime("%Y%m%d"), "600001", "A", 5))
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES(?,?,?,?,0)", (YDAY.strftime("%Y%m%d"), "600002", "B", 4))
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES(?,?,?,?,0)", (ASOF.strftime("%Y%m%d"), "600003", "C", 4))
        upsert_df(conn, "market_emotion", pd.DataFrame([
            {"date": (ASOF - dt.timedelta(days=3)).isoformat(), "limit_up_count": 90,
             "max_lianban": 5, "zhaban_rate": 0.2},
            {"date": (ASOF - dt.timedelta(days=2)).isoformat(), "limit_up_count": 85,
             "max_lianban": 5, "zhaban_rate": 0.22},
            {"date": (ASOF - dt.timedelta(days=1)).isoformat(), "limit_up_count": 80,
             "max_lianban": 5, "zhaban_rate": 0.25},
            {"date": ASOF.isoformat(), "limit_up_count": 40, "max_lianban": 4, "zhaban_rate": 0.45},
        ]))
        conn.commit()
        sigs = scan(conn, "intraday", asof=ASOF, now=dt.datetime(2026, 8, 22, 14, 0), quotes={})
        ids = {s.id for s in sigs}
        assert "space_height" in ids
        assert "space_breadth" in ids
        height = next(s for s in sigs if s.id == "space_height")
        assert height.severity in ("watch", "action")
    finally:
        conn.close()


def test_scan_network_failure_returns_empty_not_raise(monkeypatch):
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        conn.commit()

        def _boom(symbols=None):
            raise RuntimeError("network down")

        monkeypatch.setattr("invest.data.auction.fetch_batch_quotes", _boom)
        sigs = scan(conn, "auction", asof=ASOF)
        assert sigs == [] or isinstance(sigs, list)
    finally:
        conn.close()


def test_persist_signals_and_auction_snapshots():
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _seed_bars(conn, "600519", last_vol=10_000, avg_vol=10_000)
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES(?,?,?,?,0)", (YDAY.strftime("%Y%m%d"), "600519", "茅台", 2))
        conn.commit()
        quotes = {"600519": {"name": "茅台", "price": 10.0, "pct": 1.2, "vol": 400}}
        scan(conn, "auction", asof=ASOF, quotes=quotes, persist=True)
        n_sig = conn.execute("SELECT COUNT(*) FROM trade_signals WHERE date=?",
                             (ASOF.isoformat(),)).fetchone()[0]
        n_snap = conn.execute("SELECT COUNT(*) FROM auction_snapshots WHERE date=?",
                              (ASOF.isoformat(),)).fetchone()[0]
        assert n_sig >= 1
        assert n_snap == 1
        row = conn.execute(
            "SELECT horizon, layer FROM trade_signals WHERE subject='600519'"
        ).fetchone()
        assert row["horizon"] == "short"
        assert row["layer"] in ("watch", "discovery")
    finally:
        conn.close()


def _sig(**kwargs):
    from invest.signals.types import Signal

    base = {
        "id": "high_vol", "name": "高位放量", "session": "close", "severity": "watch",
        "subject_type": "stock", "subject": "600519", "hint": "量比2.5", "evidence": {},
    }
    base.update(kwargs)
    return Signal(**base)


def test_persist_writes_explicit_horizon_layer():
    from invest.signals.persist import persist_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        persist_signals(
            conn,
            [_sig(horizon="mid", layer="discovery", session="daily", id="quad_hunt")],
            ASOF,
            "daily",
        )
        row = conn.execute(
            "SELECT horizon, layer, session FROM trade_signals WHERE subject='600519'"
        ).fetchone()
        assert row["horizon"] == "mid"
        assert row["layer"] == "discovery"
        assert row["session"] == "daily"
    finally:
        conn.close()


def test_persist_daily_does_not_delete_close():
    from invest.signals.persist import persist_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        persist_signals(conn, [_sig(session="close", id="high_vol")], ASOF, "close")
        persist_signals(
            conn,
            [_sig(session="daily", id="quad_hunt", horizon="mid", layer="market")],
            ASOF,
            "daily",
        )
        sessions = {
            r[0]
            for r in conn.execute(
                "SELECT session FROM trade_signals WHERE date=?", (ASOF.isoformat(),)
            )
        }
        assert sessions == {"close", "daily"}
    finally:
        conn.close()


def test_persist_missing_horizon_layer_columns_does_not_raise():
    """未 migrate 的旧表：persist 不炸，仍能写入一期列。"""
    from invest.signals.persist import persist_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute("DROP TABLE trade_signals")
        conn.execute(
            """CREATE TABLE trade_signals (
                date TEXT NOT NULL,
                session TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                subject_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                severity TEXT NOT NULL,
                name TEXT,
                hint TEXT,
                evidence TEXT,
                src TEXT NOT NULL DEFAULT 'signals',
                PRIMARY KEY (date, session, signal_id, subject)
            )"""
        )
        conn.commit()
        persist_signals(conn, [_sig()], ASOF, "close")
        n = conn.execute("SELECT COUNT(*) FROM trade_signals").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_list_signals_filters_by_layer():
    from invest.signals.persist import persist_signals
    from invest.signals.query import list_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        persist_signals(
            conn,
            [
                _sig(layer="watch", subject="600519", id="high_vol"),
                _sig(layer="discovery", subject="000002", id="shrink_extreme"),
            ],
            ASOF,
            "close",
        )
        rows = list_signals(conn, ASOF)
        assert len(rows) == 2
        watch = list_signals(conn, ASOF, layer="watch")
        assert len(watch) == 1 and watch[0]["subject"] == "600519"
        disc = list_signals(conn, ASOF, layer="discovery")
        assert len(disc) == 1 and disc[0]["subject"] == "000002"
        latest = list_signals(conn)
        assert len(latest) == 2
        assert list_signals(conn, ASOF, horizon="mid") == []
        sess = list_signals(conn, ASOF, session="close")
        assert len(sess) == 2
    finally:
        conn.close()


def test_list_signals_missing_columns_returns_empty():
    from invest.signals.query import list_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute("DROP TABLE trade_signals")
        conn.execute(
            """CREATE TABLE trade_signals (
                date TEXT NOT NULL,
                session TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                subject_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                severity TEXT NOT NULL,
                name TEXT,
                hint TEXT,
                evidence TEXT,
                src TEXT NOT NULL DEFAULT 'signals',
                PRIMARY KEY (date, session, signal_id, subject)
            )"""
        )
        conn.commit()
        assert list_signals(conn, ASOF) == []
    finally:
        conn.close()


def test_split_b1_main_excludes_discovery_watch():
    from invest.signals.format import format_discovery_action, format_signals, split_b1

    watch_main = [
        _sig(subject=f"w{i}", layer="watch", horizon="short", severity="info", id=f"info{i}")
        for i in range(3)
    ]
    market = [
        _sig(subject="广度", layer="market", horizon="short", severity="watch",
             subject_type="market", id="space_breadth"),
    ]
    disc_watch = [
        _sig(subject=f"dw{i}", layer="discovery", horizon="short", severity="watch",
             id="shrink_extreme")
        for i in range(10)
    ]
    disc_act = [
        _sig(subject=f"da{i}", layer="discovery", horizon="short", severity="action",
             id="high_vol")
        for i in range(3)
    ]
    mid = [
        _sig(subject="半导体", layer="discovery", horizon="mid", severity="action",
             subject_type="sector", id="quad_hunt"),
    ]
    main, disc = split_b1(watch_main + market + disc_watch + disc_act + mid)
    assert all(s.layer in ("watch", "market") for s in main)
    assert all(s.horizon == "short" for s in main)
    assert not any(s.subject.startswith("dw") for s in main)
    assert not any(s.id == "quad_hunt" for s in main)
    assert not any(s.id == "quad_hunt" for s in disc)
    assert "广度" in {s.subject for s in main}
    assert len(disc) <= 5
    assert len(disc) == 3
    assert all(s.layer == "discovery" and s.severity == "action" for s in disc)
    assert format_discovery_action(watch_main + disc_watch) == ""
    text = format_discovery_action(watch_main + disc_act)
    assert text.startswith("【明确发现】")
    assert format_signals(watch_main, title="【自定义】").startswith("【自定义】")

    extra = [
        _sig(subject=f"dx{i}", layer="discovery", horizon="short", severity="action",
             id=f"act{i}")
        for i in range(6)
    ]
    _, disc6 = split_b1(extra)
    assert len(disc6) == 5


def test_undigested_actions_marks_out_of_pool():
    from invest.signals.format import undigested_actions
    from invest.signals.persist import persist_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        persist_signals(
            conn,
            [_sig(severity="action", subject="000002", name="极致缩量",
                  id="shrink_extreme", layer="discovery")],
            YDAY,
            "close",
        )
        text = undigested_actions(conn, ASOF)
        assert "000002" in text
        assert "池外" in text
        assert "昨日未消化" in text
    finally:
        conn.close()


def test_format_market_opportunity_quad_and_extra():
    from invest.signals.format import format_market_opportunity

    empty = format_market_opportunity([])
    assert empty == ""
    text = format_market_opportunity([
        _sig(id="quad_hunt", name="主战场", subject="半导体", horizon="mid",
             layer="discovery", subject_type="sector", session="daily",
             hint="rs 0.25 crowding 0.30 正常"),
        _sig(id="sector_resonance", name="板块共振", subject="半导体", horizon="mid",
             layer="discovery", subject_type="sector", session="daily", hint="RS∩资金"),
        _sig(id="emotion_stage_shift", name="情绪切换", subject="情绪", horizon="mid",
             layer="market", subject_type="market", session="daily", hint="冰点→修复"),
    ])
    assert text.startswith("【市场机会（规则）】")
    assert "主战场" in text and "半导体" in text
    assert "板块共振" in text
    assert "情绪切换" in text or "冰点" in text


def test_d32_render_and_pick_limit():
    from invest.signals.format import format_signals, pick_signals
    from invest.signals.scan import scan
    from invest.signals.types import Signal

    many = [
        Signal(id="x", name="n", session="intraday", severity=sev,
               subject_type="stock", subject=str(i), hint="h", evidence={})
        for i, sev in enumerate(["info"] * 6 + ["watch"] * 4 + ["action"] * 3)
    ]
    picked = pick_signals(many, limit=8)
    assert len(picked) == 8
    assert picked[0].severity == "action"
    assert format_signals(picked).startswith("【交易信号】")

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _seed_bars(conn, "600519", last_vol=10_000, avg_vol=10_000)
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES(?,?,?,?,0)", (YDAY.strftime("%Y%m%d"), "600519", "茅台", 2))
        conn.commit()
        scan(conn, "auction", asof=ASOF,
             quotes={"600519": {"name": "茅台", "price": 10.0, "pct": 1.2, "vol": 400}})
    finally:
        conn.close()
    from invest.skills.runner import run as run_skill

    # asof 不能靠默认今天；d32 在无命中时返回空串。用注入 quotes 的 scan 验证文本即可。
    conn2 = connect(p)
    try:
        text = format_signals(scan(conn2, "auction", asof=ASOF,
                                   quotes={"600519": {"name": "茅台", "price": 10.0, "pct": 1.2, "vol": 400}}))
    finally:
        conn2.close()
    assert "保量" in text or "交易信号" in text
    out = run_skill("d32_trade_signals", db_path=p, session="close")
    assert isinstance(out, str)
    out_blank = run_skill("d32_trade_signals", db_path=p, session="close", horizon="", layer="")
    assert isinstance(out_blank, str)

    from invest.signals.persist import persist_signals
    from invest.skills.sections.d32_trade_signals import render

    conn3 = connect(p)
    try:
        persist_signals(
            conn3,
            [_sig(session="daily", horizon="mid", layer="discovery", id="quad_hunt",
                  name="主战场", subject="半导体", hint="rs>0 crowding<0.8")],
            ASOF,
            "daily",
        )
        n_before = conn3.execute("SELECT COUNT(*) FROM trade_signals").fetchone()[0]
        daily_text = render(p, session="daily", horizon="mid", layer="discovery")
        assert "半导体" in daily_text
        n_after = conn3.execute("SELECT COUNT(*) FROM trade_signals").fetchone()[0]
        assert n_after == n_before
        assert "半导体" not in render(p, session="daily", horizon="short")
    finally:
        conn3.close()


def test_format_signals_appends_stock_name(tmp_path, monkeypatch):
    """2026-09-18：股票类信号在代码后带名称（300438 鹏辉能源）；非股票类不变、查不到只留代码。

    名称来源：① 本地缓存 data/symbol_names.json；② 库内带 name 列的表（此处用 auction_snapshots）。
    """
    import json

    from invest.data import names as names_mod
    from invest.signals.format import format_signals
    from invest.signals.types import Signal

    cache = tmp_path / "symbol_names.json"
    cache.write_text(json.dumps({"names": {"300438": "鹏辉能源"}}), encoding="utf-8")
    monkeypatch.setattr(names_mod, "CACHE_FILE", cache)
    monkeypatch.setattr(names_mod, "_file_cache", None)
    monkeypatch.setattr(names_mod, "_file_mtime", None)

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute(
            "INSERT INTO auction_snapshots(date, symbol, name, price, pct) VALUES(?,?,?,?,?)",
            (ASOF.strftime("%Y-%m-%d"), "002083", "孚日股份", 10.0, 0.5),
        )
        conn.commit()
    finally:
        conn.close()

    sigs = [
        Signal(id="auction_keep_vol", name="竞价保量", session="auction", severity="watch",
               subject_type="stock", subject="300438", hint="量比 3.2", evidence={}),
        Signal(id="auction_keep_vol_yoy", name="竞价同比保量", session="auction", severity="watch",
               subject_type="stock", subject="002083", hint="量比 2.1", evidence={}),
        Signal(id="auction_keep_vol", name="竞价保量", session="auction", severity="watch",
               subject_type="stock", subject="601091", hint="量比 1.9", evidence={}),
        Signal(id="sector_flow_spike", name="板块资金放大", session="daily", severity="watch",
               subject_type="sector", subject="半导体", hint="净流入放大", evidence={}),
    ]
    text = format_signals(sigs, limit=10, db_path=p)
    assert "300438 鹏辉能源" in text                      # 命中本地缓存
    assert "002083 孚日股份" in text                      # 命中库内名称
    assert "601091 · 竞价保量" in text                    # 查不到名称 → 只留代码，不编造
    assert "半导体 · 板块资金放大" in text                # 非股票类不加名称

    # 不传 db_path → 纯函数行为不变（不带名称）
    plain = format_signals(sigs, limit=10)
    assert "300438" in plain and "鹏辉能源" not in plain


def test_auto_overlays_background_zero_weight():
    """比价 overlay：短线信号 role=背景，不改变总分。"""
    from invest.discipline.auto import auto_factor_score

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        rows = []
        d = dt.date(2024, 1, 1)
        for i in range(200):
            rows.append({"symbol": "600519", "date": (d + dt.timedelta(days=i)).isoformat(),
                         "open": 10.0 + i * 0.01, "high": 10.1 + i * 0.01,
                         "low": 9.9 + i * 0.01, "close": 10.0 + i * 0.01,
                         "volume": 10_000, "amount": 1e8, "src": "akshare"})
        upsert_df(conn, "daily_bars", pd.DataFrame(rows))
        conn.commit()
        base = auto_factor_score(conn, "600519", cycle="波段")
        assert base["ok"]
        total_before = base["factor_result"]["total"]
        conn.execute(
            """INSERT INTO trade_signals
               (date, session, signal_id, subject_type, subject, severity, name, hint, evidence)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (ASOF.isoformat(), "close", "high_vol", "stock", "600519", "watch",
             "高位放量", "量比2.5，高位放量上涨", "{}"),
        )
        conn.commit()
        rep = auto_factor_score(conn, "600519", cycle="波段")
        overlays = [f for f in rep["factors"] if f.get("role") == "背景"]
        assert rep.get("overlays")
        assert overlays
        assert all(f["weight"] == 0.0 for f in overlays)
        assert abs(rep["factor_result"]["total"] - total_before) < 1e-6
    finally:
        conn.close()
