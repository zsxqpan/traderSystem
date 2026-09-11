"""仪表盘交易信号查询（tempfile 库，不读 data/invest.db）。"""
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
from invest.signals.persist import persist_signals
from invest.signals.types import Signal

ASOF = dt.date(2026, 8, 22)
PREV = dt.date(2026, 8, 18)


def _tmp_db(name: str = "invest_dashboard_signals_test.db"):
    p = os.path.join(tempfile.gettempdir(), name)
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _sig(**kw) -> Signal:
    data = {
        "id": "quad_hunt", "name": "主战场", "session": "daily", "severity": "watch",
        "subject_type": "sector", "subject": "半导体", "hint": "rs>0 crowding<0.8",
        "horizon": "mid", "layer": "discovery",
    }
    data.update(kw)
    return Signal(**data)


def _seed_scatter(conn):
    upsert_df(conn, "quant_strength", pd.DataFrame([{
        "run_date": ASOF.isoformat(), "obj_type": "industry", "obj": "半导体",
        "period": "short", "rs": 0.25, "momentum": 0.1, "trend_stage": "加速",
        "calc_version": "v1",
    }, {
        "run_date": ASOF.isoformat(), "obj_type": "industry", "obj": "银行",
        "period": "short", "rs": -0.05, "momentum": 0.0, "trend_stage": "震荡",
        "calc_version": "v1",
    }]))
    upsert_df(conn, "quant_valuation", pd.DataFrame([{
        "run_date": ASOF.isoformat(), "obj": "半导体", "pe_pct": 0.5,
        "crowding": 0.30, "crowding_state": "正常",
    }, {
        "run_date": ASOF.isoformat(), "obj": "银行", "pe_pct": 0.4,
        "crowding": 0.85, "crowding_state": "高拥挤",
    }]))
    persist_signals(
        conn,
        [_sig(id="quad_hunt", subject="半导体", name="主战场")],
        ASOF,
        "daily",
    )


def test_d1_crowding_strength_has_state_and_quad_id():
    from dashboard import queries as q

    p = _tmp_db()
    conn = connect(p)
    try:
        _seed_scatter(conn)
        conn.commit()
    finally:
        conn.close()
    df = q.load_crowding_vs_strength(p)
    assert {"obj", "rs", "crowding", "trend_stage", "crowding_state", "quad_id"} <= set(df.columns)
    semi = df[df["obj"] == "半导体"].iloc[0]
    assert semi["crowding_state"] == "正常"
    assert str(semi["quad_id"]).startswith("quad_")
    bank = df[df["obj"] == "银行"].iloc[0]
    assert bank["crowding_state"] == "高拥挤"
    assert not (str(bank["quad_id"]) if pd.notna(bank["quad_id"]) else "").startswith("quad_")


def test_d2_load_signals_columns_and_filters():
    from dashboard import queries as q

    p = _tmp_db()
    conn = connect(p)
    try:
        persist_signals(
            conn,
            [
                _sig(id="shrink_extreme", name="极致缩量", session="close",
                     horizon="short", layer="watch", severity="action",
                     subject_type="stock", subject="600519", hint="缩量"),
                _sig(id="quad_hunt", session="daily", horizon="mid",
                     layer="discovery", subject="半导体"),
            ],
            ASOF,
            "close",
        )
        persist_signals(
            conn,
            [_sig(id="quad_hunt", session="daily", horizon="mid",
                  layer="discovery", subject="半导体")],
            ASOF,
            "daily",
        )
        conn.commit()
    finally:
        conn.close()

    cols = ["date", "session", "horizon", "layer", "severity",
            "signal_id", "name", "subject", "hint", "evidence"]
    empty = q.load_signals(_tmp_db("invest_dashboard_signals_empty.db"))
    assert list(empty.columns) == cols
    assert empty.empty

    df = q.load_signals(p)
    assert list(df.columns) == cols
    assert not df.empty
    short = q.load_signals(p, horizon="short")
    assert set(short["horizon"]) == {"short"}
    mid = q.load_signals(p, horizon="mid", session="daily")
    assert list(mid["signal_id"]) == ["quad_hunt"]
    watch = q.load_signals(p, layer="watch")
    assert list(watch["subject"]) == ["600519"]


def test_d2_load_signals_days_quad_path():
    from dashboard import queries as q

    p = _tmp_db()
    conn = connect(p)
    try:
        persist_signals(
            conn,
            [_sig(id="quad_path", name="拥挤回落仍强", subject="半导体",
                  hint="chase→hunt")],
            PREV,
            "daily",
        )
        persist_signals(
            conn,
            [_sig(id="quad_path", name="拥挤回落仍强", subject="白酒",
                  hint="chase→hunt")],
            ASOF,
            "daily",
        )
        conn.commit()
    finally:
        conn.close()
    df = q.load_signals(p, horizon="mid", days=5)
    paths = df[df["signal_id"] == "quad_path"]
    assert set(paths["subject"]) == {"半导体", "白酒"}


def test_d3_nav_inserts_signals_page():
    from dashboard.nav import PAGES_ORDER

    assert PAGES_ORDER[0] == "市场总览"
    assert PAGES_ORDER[1] == "交易信号"
    assert "短线轨" in PAGES_ORDER
    text = Path(__file__).resolve().parents[1].joinpath("dashboard/app.py").read_text(encoding="utf-8")
    assert '"交易信号": page_signals' in text
    assert "st.rerun" not in text


def test_d4_scatter_hover_symbol_and_lines():
    from dashboard.charts import crowding_strength_figure

    df = pd.DataFrame([
        {"obj": "半导体", "rs": 0.25, "crowding": 0.3, "trend_stage": "加速",
         "crowding_state": "正常", "quad_id": "quad_hunt"},
        {"obj": "银行", "rs": -0.05, "crowding": 0.85, "trend_stage": "震荡",
         "crowding_state": "高拥挤", "quad_id": ""},
    ])
    fig = crowding_strength_figure(df)
    assert fig is not None
    hover = " ".join(str(t.hovertemplate or "") for t in fig.data)
    assert "crowding_state" in hover
    assert "quad_id" in hover
    symbols = []
    for t in fig.data:
        symbols.append(str(t.marker.symbol))
    joined = " ".join(symbols)
    assert "diamond" in joined
    assert "circle" in joined
    xs = [getattr(s, "x0", None) for s in (fig.layout.shapes or [])]
    ys = [getattr(s, "y0", None) for s in (fig.layout.shapes or [])]
    assert 0 in xs or 0.0 in xs
    assert 0.8 in ys or any(y is not None and abs(float(y) - 0.8) < 1e-9 for y in ys if y is not None)


def test_d4_quad_path_omit_when_empty():
    from dashboard.charts import quad_path_frame

    empty = pd.DataFrame(columns=[
        "date", "session", "horizon", "layer", "severity",
        "signal_id", "name", "subject", "hint", "evidence",
    ])
    assert quad_path_frame(empty).empty
    df = pd.DataFrame([{
        "date": ASOF.isoformat(), "session": "daily", "horizon": "mid",
        "layer": "discovery", "severity": "watch", "signal_id": "quad_path",
        "name": "拥挤回落仍强", "subject": "半导体", "hint": "x", "evidence": "{}",
    }])
    out = quad_path_frame(df)
    assert list(out["subject"]) == ["半导体"]


def test_d5_mid_quadrants_always_four():
    from dashboard.charts import mid_quadrant_blocks

    df = pd.DataFrame([{
        "signal_id": "quad_hunt", "subject": "半导体", "hint": "主战场",
        "name": "主战场",
    }])
    blocks = mid_quadrant_blocks(df)
    titles = [t for t, _ in blocks]
    assert titles == ["主战场", "追高风险", "观察", "回避"]
    hunt = blocks[0][1]
    assert list(hunt["subject"]) == ["半导体"]
    assert all(b.empty for _, b in blocks[1:])


def test_d6_rotation_lead_caption():
    from dashboard.charts import rotation_lead_caption

    assert rotation_lead_caption(pd.DataFrame()) == ""
    df = pd.DataFrame([
        {"signal_id": "rotation_lead", "subject": "半导体"},
        {"signal_id": "rotation_lag", "subject": "银行"},
        {"signal_id": "rotation_lead", "subject": "有色"},
    ])
    cap = rotation_lead_caption(df)
    assert "半导体" in cap and "有色" in cap
    assert "银行" not in cap
