"""中线信号规则（invest/signals/mid）：只读 quant 表，临时库，不联网。"""
from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.data.storage import upsert_df
from invest.db import connect, init_db

D = "2026-08-22"


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_signals_mid_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _strength(conn, rows: list[dict], period: str = "short", obj_type: str = "industry"):
    payload = []
    for r in rows:
        payload.append({
            "run_date": r.get("run_date", D),
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
            "run_date": r.get("run_date", D),
            "obj": r["obj"],
            "pe_pct": 0.5,
            "crowding": r["crowding"],
            "crowding_state": r.get("crowding_state", ""),
        })
    upsert_df(conn, "quant_valuation", pd.DataFrame(payload))


def test_quad_four_industries_one_each():
    from invest.signals.mid import quad_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        _strength(conn, [
            {"obj": "半导体", "rs": 0.25, "trend_stage": "加速"},
            {"obj": "白酒", "rs": 0.18, "trend_stage": "加速"},
            {"obj": "银行", "rs": -0.05, "trend_stage": "启动"},
            {"obj": "煤炭", "rs": -0.12, "trend_stage": "减速"},
        ])
        _valuation(conn, [
            {"obj": "半导体", "crowding": 0.30, "crowding_state": "正常"},
            {"obj": "白酒", "crowding": 0.88, "crowding_state": "高拥挤"},
            {"obj": "银行", "crowding": 0.40, "crowding_state": "正常"},
            {"obj": "煤炭", "crowding": 0.90, "crowding_state": "高拥挤"},
        ])
        sigs = quad_signals(conn)
        by_id = {}
        for s in sigs:
            by_id.setdefault(s.id, []).append(s)
        assert "quad_hunt" in by_id and any(s.subject == "半导体" for s in by_id["quad_hunt"])
        assert "quad_chase" in by_id and any(s.subject == "白酒" for s in by_id["quad_chase"])
        assert "quad_watch_cheap" in by_id and any(s.subject == "银行" for s in by_id["quad_watch_cheap"])
        assert "quad_avoid" in by_id and any(s.subject == "煤炭" for s in by_id["quad_avoid"])
        hunt = next(s for s in sigs if s.id == "quad_hunt")
        assert hunt.horizon == "mid" and hunt.layer == "discovery" and hunt.session == "daily"
        assert hunt.subject_type == "sector"
        assert "0.25" in hunt.hint or "rs" in hunt.hint.lower()
        assert "0.3" in hunt.hint or "30%" in hunt.hint or "0.30" in hunt.hint
        assert "正常" in hunt.hint
        avoid = next(s for s in sigs if s.subject == "煤炭")
        assert avoid.layer == "market"
        # 同一行业只进一个象限
        subjects = [s.subject for s in sigs]
        assert len(subjects) == len(set(subjects))
    finally:
        conn.close()


def test_quad_extreme_worsening_rs_positive_is_avoid_not_chase():
    from invest.signals.mid import quad_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        _strength(conn, [{"obj": "有色", "rs": 0.22, "trend_stage": "加速"}])
        _valuation(conn, [{"obj": "有色", "crowding": 0.97, "crowding_state": "极端且恶化"}])
        sigs = quad_signals(conn)
        assert any(s.id == "quad_avoid" and s.subject == "有色" for s in sigs)
        assert not any(s.id == "quad_chase" and s.subject == "有色" for s in sigs)
    finally:
        conn.close()


def test_quad_empty_tables_no_raise():
    from invest.signals.mid import quad_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        assert quad_signals(conn) == []
    finally:
        conn.close()


def test_quad_path_crowding_falls_still_strong():
    """5 日 crowding 0.90→0.70、rs 一直为正 → 1 条 path。"""
    from invest.signals.mid import quad_path_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        dates = [f"2026-08-{d:02d}" for d in range(18, 23)]  # 18..22 共 5 日
        crowds = [0.90, 0.85, 0.80, 0.75, 0.70]
        for day, c in zip(dates, crowds, strict=True):
            _strength(conn, [{"run_date": day, "obj": "半导体", "rs": 0.15, "trend_stage": "加速"}])
            _valuation(conn, [{"run_date": day, "obj": "半导体", "crowding": c,
                               "crowding_state": "高拥挤" if c >= 0.8 else "升温"}])
        sigs = quad_path_signals(conn)
        paths = [s for s in sigs if s.id == "quad_path" and s.subject == "半导体"]
        assert len(paths) == 1
        assert "拥挤回落仍强" in paths[0].hint
        assert paths[0].horizon == "mid"
        assert paths[0].layer == "discovery"
        # 无变化不出
        for day in dates:
            _strength(conn, [{"run_date": day, "obj": "银行", "rs": 0.10, "trend_stage": "启动"}])
            _valuation(conn, [{"run_date": day, "obj": "银行", "crowding": 0.50,
                               "crowding_state": "正常"}])
        sigs2 = quad_path_signals(conn)
        assert not any(s.subject == "银行" for s in sigs2)
    finally:
        conn.close()


def test_stage_start_on_change_not_when_unchanged():
    """昨震荡今启动 → stage_start；连续两日启动 → 无。"""
    from invest.signals.mid import stage_change_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        y, t = "2026-08-21", D
        _strength(conn, [
            {"run_date": y, "obj": "电子", "rs": 0.02, "trend_stage": "震荡"},
            {"run_date": t, "obj": "电子", "rs": 0.04, "trend_stage": "启动"},
            {"run_date": y, "obj": "家电", "rs": 0.01, "trend_stage": "启动"},
            {"run_date": t, "obj": "家电", "rs": 0.02, "trend_stage": "启动"},
        ])
        sigs = stage_change_signals(conn)
        hits = [s for s in sigs if s.id == "stage_start" and s.subject == "电子"]
        assert len(hits) == 1
        assert hits[0].subject_type == "sector"
        assert hits[0].horizon == "mid"
        assert hits[0].session == "daily"
        assert not any(s.subject == "家电" for s in sigs)
    finally:
        conn.close()


def test_rotation_lead_and_lag():
    from invest.signals.mid import rotation_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        upsert_df(conn, "quant_rotation", pd.DataFrame([
            {"run_date": D, "industry": "半导体", "rank": 3, "lead_lag": "领涨",
             "turnover_share": 0.08},
            {"run_date": D, "industry": "银行", "rank": 70, "lead_lag": "滞后",
             "turnover_share": 0.01},
            {"run_date": D, "industry": "煤炭", "rank": 20, "lead_lag": "领涨",
             "turnover_share": 0.04},
        ]))
        sigs = rotation_signals(conn)
        assert any(s.id == "rotation_lead" and s.subject == "半导体" for s in sigs)
        assert any(s.id == "rotation_lag" and s.subject == "银行" for s in sigs)
        assert not any(s.subject == "煤炭" for s in sigs)
        lead = next(s for s in sigs if s.id == "rotation_lead")
        assert lead.horizon == "mid" and lead.subject_type == "sector"
    finally:
        conn.close()


def test_resonance_intersection_and_mid_rs_top_skips_break():
    from invest.signals.mid import mid_rs_top_signals, resonance_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        _strength(conn, [{"obj": "半导体", "rs": 0.20, "trend_stage": "加速"}])
        upsert_df(conn, "sector_fund_flow", pd.DataFrame([
            {"date": D, "industry": "半导体", "main_net": 3e8, "src": "eastmoney"},
            {"date": D, "industry": "银行", "main_net": 1e8, "src": "eastmoney"},
        ]))
        res = resonance_signals(conn)
        assert len([s for s in res if s.id == "sector_resonance"]) == 1
        assert res[0].subject == "半导体"

        _strength(conn, [
            {"obj": "半导体", "rs": 0.30, "trend_stage": "加速"},
            {"obj": "煤炭", "rs": 0.25, "trend_stage": "破位"},
        ], period="mid")
        tops = mid_rs_top_signals(conn)
        assert any(s.id == "mid_rs_top" and s.subject == "半导体" for s in tops)
        assert not any(s.subject == "煤炭" for s in tops)
        assert all(s.severity == "info" for s in tops)
    finally:
        conn.close()


def test_style_shift_and_emotion_stage_shift():
    from invest.signals.mid import emotion_stage_shift_signals, style_shift_signals

    p = _tmp_db()
    conn = connect(p)
    try:
        d1, d2 = "2026-08-21", D
        _strength(conn, [
            {"run_date": d1, "obj": "000852", "rs": 0.10},
            {"run_date": d1, "obj": "000905", "rs": 0.08},
            {"run_date": d1, "obj": "000016", "rs": -0.04},
            {"run_date": d1, "obj": "000300", "rs": -0.03},
            {"run_date": d2, "obj": "000852", "rs": -0.10},
            {"run_date": d2, "obj": "000905", "rs": -0.08},
            {"run_date": d2, "obj": "000016", "rs": 0.04},
            {"run_date": d2, "obj": "000300", "rs": 0.05},
        ], obj_type="index")
        styles = style_shift_signals(conn)
        assert len(styles) == 1
        assert styles[0].id == "style_shift"
        assert styles[0].layer == "market"
        assert styles[0].subject == "风格"

        _strength(conn, [
            {"run_date": d1, "obj": "000852", "rs": 0.10},
            {"run_date": d1, "obj": "000905", "rs": 0.08},
            {"run_date": d1, "obj": "000016", "rs": -0.04},
            {"run_date": d1, "obj": "000300", "rs": -0.03},
            {"run_date": d2, "obj": "000852", "rs": 0.11},
            {"run_date": d2, "obj": "000905", "rs": 0.09},
            {"run_date": d2, "obj": "000016", "rs": -0.04},
            {"run_date": d2, "obj": "000300", "rs": -0.02},
        ], obj_type="index")
        assert style_shift_signals(conn) == []

        upsert_df(conn, "market_emotion", pd.DataFrame([
            {"date": "2026-08-21", "limit_up_count": 20, "max_lianban": 2, "zhaban_rate": 0.50},
            {"date": D, "limit_up_count": 55, "max_lianban": 4, "zhaban_rate": 0.28},
        ]))
        emo = emotion_stage_shift_signals(conn)
        assert len(emo) == 1
        assert emo[0].id == "emotion_stage_shift"
        assert emo[0].evidence.get("from") == "冰点"
        assert emo[0].evidence.get("to") == "启动"
        assert emo[0].layer == "market"

        upsert_df(conn, "market_emotion", pd.DataFrame([
            {"date": "2026-08-21", "limit_up_count": 20, "max_lianban": 2, "zhaban_rate": 0.50},
            {"date": D, "limit_up_count": 22, "max_lianban": 2, "zhaban_rate": 0.48},
        ]))
        assert emotion_stage_shift_signals(conn) == []
    finally:
        conn.close()


def test_scan_daily_empty_and_mid_rules(monkeypatch):
    import importlib

    from invest.signals.scan import scan

    scan_mod = importlib.import_module("invest.signals.scan")

    def _boom(_symbols=None):
        raise AssertionError("daily must not fetch quotes")

    monkeypatch.setattr(scan_mod, "_fetch_quotes", _boom)
    p = _tmp_db()
    conn = connect(p)
    try:
        assert scan(conn, "daily") == []
        _strength(conn, [{"obj": "半导体", "rs": 0.25, "trend_stage": "加速"}])
        _valuation(conn, [{"obj": "半导体", "crowding": 0.30, "crowding_state": "正常"}])
        sigs = scan(conn, "daily", persist=True, limit=10_000)
        assert any(s.id == "quad_hunt" and s.horizon == "mid" for s in sigs)
        n = conn.execute(
            "SELECT COUNT(*) FROM trade_signals WHERE session='daily'"
        ).fetchone()[0]
        assert n >= 1
    finally:
        conn.close()


def test_format_mid_quadrants_groups_and_skips_empty():
    from invest.signals.format import format_mid_quadrants
    from invest.signals.types import Signal

    def _q(qid, subject):
        return Signal(
            id=qid, name="n", session="daily", severity="watch",
            subject_type="sector", subject=subject, hint="h",
            horizon="mid", layer="discovery",
        )

    all_four = format_mid_quadrants([
        _q("quad_hunt", "半导体"),
        _q("quad_chase", "白酒"),
        _q("quad_watch_cheap", "银行"),
        _q("quad_avoid", "煤炭"),
    ])
    assert all_four.startswith("【中线战场】")
    assert "主战场" in all_four and "追高风险" in all_four
    assert "观察" in all_four and "回避" in all_four
    only_hunt = format_mid_quadrants([_q("quad_hunt", "半导体")])
    assert "主战场" in only_hunt
    assert "回避" not in only_hunt
    assert format_mid_quadrants([]) == ""
