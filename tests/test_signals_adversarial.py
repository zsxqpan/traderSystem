"""信号引擎对抗审查：会上线出错 / 违反冻结原则的回归。

临时库 + init_db，注入行情/映射，不联网、不碰 data/invest.db。
"""
from __future__ import annotations

import ast
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.data.storage import upsert_df
from invest.db import connect, init_db

ASOF = dt.date(2026, 8, 22)
YDAY = dt.date(2026, 8, 21)
ROOT = Path(__file__).resolve().parents[1]
SIGNALS_DIR = ROOT / "invest" / "signals"


@pytest.fixture(autouse=True)
def _no_net(monkeypatch):
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})
    monkeypatch.setattr("invest.data.auction.fetch_batch_quotes", lambda symbols=None: {})
    monkeypatch.setattr(
        "invest.data.etf.fetch_etf_quotes",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("signals must not fetch etf")),
    )


def _tmp_db():
    fd, p = tempfile.mkstemp(suffix="_sig_adv.db")
    os.close(fd)
    os.remove(p)
    init_db(p)
    return p


def _cleanup(p: str) -> None:
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass


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


def _zt(conn, symbol: str, name: str = "", lianban: int = 1, day=None):
    day = day or YDAY
    conn.execute(
        "INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) VALUES(?,?,?,?,0)",
        (day.strftime("%Y%m%d"), symbol, name or symbol, lianban),
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


def _valuation(conn, rows: list[dict]):
    payload = []
    for r in rows:
        payload.append({
            "run_date": r.get("run_date", ASOF.isoformat()),
            "obj": r["obj"],
            "pe_pct": 0.5,
            "crowding": r["crowding"],
            "crowding_state": r.get("crowding_state", ""),
        })
    upsert_df(conn, "quant_valuation", pd.DataFrame(payload))


def test_unknown_industry_not_fake_other_collective(monkeypatch):
    """fetch_industries 空 / 映射空时，不得把无关昨涨停捏成「其他」集体放量 action。"""
    from invest.signals.scan import scan

    monkeypatch.setattr("invest.data.industry_map.load_industry_stocks", lambda path=None: {})
    p = _tmp_db()
    conn = connect(p)
    try:
        for i, sym in enumerate(("600001", "600002", "600003")):
            _seed_bars(conn, sym, last_vol=25_000, avg_vol=10_000, last_close=10.0)
            _zt(conn, sym, f"股{i}")
        conn.commit()
        now = dt.datetime(2026, 8, 22, 14, 0)
        quotes = {s: {"name": s, "price": 10.2, "pct": 2.0, "vol": 20_000}
                  for s in ("600001", "600002", "600003")}
        sigs = scan(conn, "intraday", asof=ASOF, now=now, quotes=quotes)
        fake = [s for s in sigs if s.id == "sector_collective"]
        assert fake == [], [s.subject + s.hint for s in fake]
    finally:
        conn.close()
        _cleanup(p)


def test_mid_one_rule_crash_keeps_quads(monkeypatch):
    """单条中线规则抛错不得吞掉四象限。"""
    from invest.signals.mid import mid_signals

    def _boom(_conn):
        raise RuntimeError("emotion_cycle broken")

    monkeypatch.setattr("invest.signals.mid.emotion_stage_shift_signals", _boom)
    p = _tmp_db()
    conn = connect(p)
    try:
        _strength(conn, [{"obj": "半导体", "rs": 0.25, "trend_stage": "加速"}])
        _valuation(conn, [{"obj": "半导体", "crowding": 0.30, "crowding_state": "正常"}])
        sigs = mid_signals(conn)
        assert any(s.id == "quad_hunt" and s.subject == "半导体" for s in sigs)
    finally:
        conn.close()
        _cleanup(p)


def test_scan_daily_persist_skips_wipe_when_mid_raises(monkeypatch):
    """mid_signals 整体失败时，不得 DELETE 当日已落库的 daily 中线。"""
    import importlib

    from invest.signals.scan import scan

    scan_mod = importlib.import_module("invest.signals.scan")
    p = _tmp_db()
    conn = connect(p)
    try:
        _strength(conn, [{"obj": "半导体", "rs": 0.25, "trend_stage": "加速"}])
        _valuation(conn, [{"obj": "半导体", "crowding": 0.30, "crowding_state": "正常"}])
        scan(conn, "daily", asof=ASOF, persist=True, limit=10_000)
        n_before = conn.execute(
            "SELECT COUNT(*) FROM trade_signals WHERE session='daily' AND signal_id='quad_hunt'"
        ).fetchone()[0]
        assert n_before >= 1

        def _boom(_conn):
            raise RuntimeError("mid bus down")

        monkeypatch.setattr("invest.signals.mid.mid_signals", _boom)
        scan_mod.scan(conn, "daily", asof=ASOF, persist=True, limit=10_000)
        n_after = conn.execute(
            "SELECT COUNT(*) FROM trade_signals WHERE session='daily' AND signal_id='quad_hunt'"
        ).fetchone()[0]
        assert n_after >= 1
    finally:
        conn.close()
        _cleanup(p)


def test_discovery_keeps_rs_cores_when_zt_fills_cap(monkeypatch):
    """昨涨停 ≥80 时仍须留下 RS TOP 行业核心股（拍板：发现不能只剩涨停基因）。"""
    from invest.signals.thresholds import DISCOVERY_STOCK_CAP
    from invest.signals.universe import discovery_symbols

    mapping = {"688001": "半导体"}
    monkeypatch.setattr("invest.data.industry_map.load_industry_stocks", lambda path=None: mapping)
    p = _tmp_db()
    conn = connect(p)
    try:
        for i in range(DISCOVERY_STOCK_CAP):
            _zt(conn, f"{i:06d}")
        _strength(conn, [{"obj": "半导体", "rs": 0.55}])
        _seed_bars(conn, "688001", last_vol=1000, avg_vol=1000, amount=9e8)
        conn.commit()
        syms = discovery_symbols(conn, ASOF)
        assert len(syms) <= DISCOVERY_STOCK_CAP
        assert "688001" in syms
    finally:
        conn.close()
        _cleanup(p)


def test_intraday_does_not_emit_lianban_fail_before_close():
    """连板晋级/断板只挂 close；盘中尚未涨停不得预报断板。"""
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _zt(conn, "000001", "平安", lianban=2, day=YDAY)
        _zt(conn, "600000", "占位", lianban=1, day=ASOF)
        conn.commit()
        now = dt.datetime(2026, 8, 22, 14, 0)
        intra = scan(conn, "intraday", asof=ASOF, now=now, quotes={})
        assert not any(s.id in ("lianban_fail", "lianban_promote") for s in intra)
        close = scan(conn, "close", asof=ASOF, quotes={})
        assert any(s.id == "lianban_fail" and s.subject == "000001" for s in close)
    finally:
        conn.close()
        _cleanup(p)


def test_board_high_open_layer_stays_discovery_if_in_watch():
    """C6：高开放量 layer=discovery，不得被 _tag_short 因在池内改成 watch。"""
    from invest.signals.scan import scan

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        conn.commit()
        boards = [
            {"symbol": "600519", "name": "茅台", "pct": 4.2, "vol": 8000, "amount": 25_000_000},
        ]
        sigs = scan(conn, "auction", asof=ASOF, quotes={}, boards=boards)
        hit = [s for s in sigs if s.id == "board_high_open_vol" and s.subject == "600519"]
        assert hit and hit[0].layer == "discovery"
    finally:
        conn.close()
        _cleanup(p)


def test_scan_computes_discovery_symbols_once(monkeypatch):
    """auction/intraday/close 每扫一次只算一遍发现宇宙（避免热门核心联网×3）。"""
    import importlib

    uni = importlib.import_module("invest.signals.universe")
    scan_mod = importlib.import_module("invest.signals.scan")
    n = {"n": 0}
    orig = uni.discovery_symbols

    def _wrap(*a, **k):
        n["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(uni, "discovery_symbols", _wrap)
    monkeypatch.setattr(scan_mod, "discovery_symbols", _wrap)
    p = _tmp_db()
    conn = connect(p)
    try:
        _zt(conn, "000002")
        conn.commit()
        scan_mod.scan(conn, "close", asof=ASOF, quotes={})
        assert n["n"] == 1, n["n"]
    finally:
        conn.close()
        _cleanup(p)


def test_quad_boundary_rs0_crowding08():
    """竖线 rs=0、横线 crowding=0.8：=线归 cheap/avoid 或 chase，互斥。"""
    from invest.signals.mid import quad_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        _strength(conn, [
            {"obj": "零轴观察", "rs": 0.0, "trend_stage": "启动"},
            {"obj": "线上追高", "rs": 0.01, "trend_stage": "加速"},
            {"obj": "线上回避", "rs": 0.0, "trend_stage": "减速"},
            {"obj": "线下猎", "rs": 0.01, "trend_stage": "加速"},
        ])
        _valuation(conn, [
            {"obj": "零轴观察", "crowding": 0.79, "crowding_state": "升温"},
            {"obj": "线上追高", "crowding": 0.80, "crowding_state": "升温"},
            {"obj": "线上回避", "crowding": 0.80, "crowding_state": "升温"},
            {"obj": "线下猎", "crowding": 0.799, "crowding_state": "升温"},
        ])
        sigs = quad_signals(conn)
        by = {s.subject: s.id for s in sigs}
        assert by["零轴观察"] == "quad_watch_cheap"
        assert by["线上追高"] == "quad_chase"
        assert by["线上回避"] == "quad_avoid"
        assert by["线下猎"] == "quad_hunt"
        assert len(by) == len(sigs)
    finally:
        conn.close()
        _cleanup(p)


def test_split_b1_excludes_mid_avoid_from_main():
    from invest.signals.format import split_b1
    from invest.signals.types import Signal

    avoid = Signal(
        id="quad_avoid", name="回避", session="daily", severity="action",
        subject_type="sector", subject="煤炭", hint="crowding 0.9",
        horizon="mid", layer="market",
    )
    main, disc = split_b1([avoid])
    assert avoid not in main and avoid not in disc


def test_mid_signals_do_not_name_stocks():
    from invest.signals.mid import mid_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        _strength(conn, [{"obj": "半导体", "rs": 0.25, "trend_stage": "加速"}])
        _valuation(conn, [{"obj": "半导体", "crowding": 0.30, "crowding_state": "正常"}])
        for s in mid_signals(conn):
            assert s.subject_type in ("sector", "market", "etf"), s
            assert not (s.subject.isdigit() and len(s.subject) == 6), s.subject
    finally:
        conn.close()
        _cleanup(p)


def test_signals_package_forbids_buy_wording_and_frozen_imports():
    forbidden = ("建议买入",)
    frozen_mods = ("invest.discipline.kelly", "invest.discipline.clusters", "invest.review.bcs")
    texts = []
    for path in sorted(SIGNALS_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        texts.append((path.name, src))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name.startswith(m) for m in frozen_mods), path.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not any(
                    node.module == m or node.module.startswith(m + ".") for m in frozen_mods
                ), path.name
    joined = "\n".join(src for _, src in texts)
    for token in forbidden:
        assert token not in joined
