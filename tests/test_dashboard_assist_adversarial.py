"""路D 对抗：仪表盘消费方（tempfile，不读 data/invest.db，不起 Streamlit）。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.data.storage import upsert_df
from invest.db import connect, init_db

ROOT = Path(__file__).resolve().parents[1]
ASOF = dt.date(2026, 8, 22)


def _tmp_db(name: str) -> str:
    p = os.path.join(tempfile.gettempdir(), name)
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed_scatter(conn):
    upsert_df(conn, "quant_strength", pd.DataFrame([{
        "run_date": ASOF.isoformat(), "obj_type": "industry", "obj": "半导体",
        "period": "short", "rs": 0.25, "momentum": 0.1, "trend_stage": "加速",
        "calc_version": "v1",
    }]))
    upsert_df(conn, "quant_valuation", pd.DataFrame([{
        "run_date": ASOF.isoformat(), "obj": "半导体", "pe_pct": 0.5,
        "crowding": 0.30, "crowding_state": "正常",
    }]))


def test_b1_scan_limit_covers_watch_and_discovery(monkeypatch):
    """b1 必须先拿够信号再 split_b1，不能用默认 DISPLAY_LIMIT=8 截断。"""
    import importlib

    from invest.signals.thresholds import DISPLAY_B1_DISCOVERY_ACTION, DISPLAY_B1_WATCH
    from invest.skills.runner import run_structured

    seen: dict = {}
    scan_mod = importlib.import_module("invest.signals.scan")

    def _scan(*a, **k):
        seen.update(k)
        return []

    monkeypatch.setattr(scan_mod, "scan_db", _scan)
    import invest.skills.sections._intraday_llm as _il
    _il.mood_llm = lambda db, ctx: {}
    _il.mainline_llm = lambda db, ctx: {"main_lines": [], "core_outlook": ""}
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35},
    })
    monkeypatch.setattr("invest.data.auction.fetch_batch_quotes", lambda symbols=None: {})
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})
    monkeypatch.setattr("invest.report._live_quotes", lambda *a, **k: ({}, {}))
    p = _tmp_db("assist_adv_b1_limit.db")
    run_structured("b1_intraday", db_path=p, brief=True)
    need = DISPLAY_B1_WATCH + DISPLAY_B1_DISCOVERY_ACTION
    assert seen.get("limit", 8) >= need, seen


def test_b1_refreshes_action_prices_outside_core(monkeypatch):
    """池外观察票也要喂给 update_statuses，否则盘中永远 pending。"""
    import importlib

    import invest.skills.sections._intraday_llm as _il
    from invest.actions.persist import persist_actions
    from invest.actions.types import Action
    from invest.skills.runner import run_structured

    _il.mood_llm = lambda db, ctx: {}
    _il.mainline_llm = lambda db, ctx: {"main_lines": [], "core_outlook": ""}
    scan_mod = importlib.import_module("invest.signals.scan")
    monkeypatch.setattr(scan_mod, "scan_db", lambda *a, **k: [])
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35},
    })
    monkeypatch.setattr("invest.data.auction.fetch_batch_quotes", lambda symbols=None: {})
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})
    monkeypatch.setattr("invest.report._live_quotes", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(
        "invest.intraday.fetch_batch_prices",
        lambda symbols, db_path=None: {s: 9.0 for s in symbols},
    )
    today = dt.date.today()
    p = _tmp_db("assist_adv_b1_action_px.db")
    conn = connect(p)
    try:
        persist_actions(
            conn,
            [Action(
                date=today.isoformat(), symbol="000999", name="池外",
                verb="watch", priority=3, source="signal",
                stop_loss=10.0, hint="观察",
            )],
            today,
        )
    finally:
        conn.close()
    run_structured("b1_intraday", db_path=p, brief=True)
    conn = connect(p)
    try:
        row = conn.execute(
            "SELECT status FROM daily_actions WHERE symbol='000999'"
        ).fetchone()
        assert row is not None and row["status"] == "triggered"
    finally:
        conn.close()


def test_scatter_survives_missing_trade_signals():
    """缺 trade_signals 仍返回散点列，不抛。"""
    from dashboard import queries as q

    p = _tmp_db("assist_adv_scatter_missing.db")
    conn = connect(p)
    try:
        _seed_scatter(conn)
        conn.execute("DROP TABLE trade_signals")
        conn.commit()
    finally:
        conn.close()
    df = q.load_crowding_vs_strength(p)
    assert {"obj", "rs", "crowding", "trend_stage", "crowding_state", "quad_id"} <= set(df.columns)
    assert list(df["obj"]) == ["半导体"]


def test_load_signals_days_survives_missing_table():
    from dashboard import queries as q

    p = _tmp_db("assist_adv_signals_missing.db")
    conn = connect(p)
    try:
        conn.execute("DROP TABLE trade_signals")
        conn.commit()
    finally:
        conn.close()
    df = q.load_signals(p, horizon="mid", days=5)
    assert list(df.columns) == q._SIGNAL_COLS
    assert df.empty


def test_load_actions_watch_survive_missing_tables():
    from dashboard import queries as q

    p = _tmp_db("assist_adv_actions_missing.db")
    conn = connect(p)
    try:
        conn.execute("DROP TABLE daily_actions")
        conn.execute("DROP TABLE watch_items")
        conn.commit()
    finally:
        conn.close()
    acts = q.load_actions(p)
    watch = q.load_watch(p)
    assert acts.empty
    assert watch.empty


def test_load_bigv_survives_missing_table():
    from dashboard import queries as q

    p = _tmp_db("assist_adv_bigv_missing.db")
    conn = connect(p)
    try:
        conn.execute("DROP TABLE IF EXISTS big_v_opinion_fts")
        conn.execute("DROP TABLE IF EXISTS big_v_opinion")
        conn.execute("DROP TABLE IF EXISTS big_v_profile")
        conn.commit()
    finally:
        conn.close()
    df = q.load_bigv_profiles(p)
    assert df.empty
    out = q.register_bigv(p, "段永平", "12345")
    asked = q.ask_bigv(p, "xq_1", "茅台怎么看")
    assert isinstance(out, dict)
    assert isinstance(asked, dict)


def test_promote_watch_hardcodes_track_not_core():
    """升级按钮必须走 track，不得直接写 core。"""
    text = (ROOT / "dashboard/app.py").read_text(encoding="utf-8")
    assert 'promote_watch(c, w_sym.strip(), level="track")' in text
    assert 'promote_watch(c, w_sym.strip(), level="core")' not in text
    assert "不自动入 core" in text


def test_dashboard_copy_forbids_suggest_buy_and_frozen_nav():
    from dashboard.nav import PAGES_ORDER

    app = (ROOT / "dashboard/app.py").read_text(encoding="utf-8")
    nav = (ROOT / "dashboard/nav.py").read_text(encoding="utf-8")
    charts = (ROOT / "dashboard/charts.py").read_text(encoding="utf-8")
    queries = (ROOT / "dashboard/queries.py").read_text(encoding="utf-8")
    blob = app + nav + charts + queries
    assert "建议买入" not in blob
    for name in ("凯利", "仓位凯利", "工单", "BCS", "VMS", "比价主链", "簇预算"):
        assert name not in PAGES_ORDER
    assert "add_vline" in charts and "x=0" in charts
    assert "add_hline" in charts and "y=0.8" in charts
