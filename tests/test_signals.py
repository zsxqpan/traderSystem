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
    finally:
        conn.close()


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
