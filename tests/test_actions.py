"""动作清单（invest/actions）：合成/落库/格式化，临时库，不联网。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.data.storage import upsert_df
from invest.db import connect, init_db, table_names

ASOF = dt.date(2026, 8, 21)
NEXT = dt.date(2026, 8, 24)  # 周一


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_actions_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed_close(conn, symbol: str, close: float, day: dt.date = ASOF):
    upsert_df(conn, "daily_bars", pd.DataFrame([{
        "symbol": symbol, "date": day.isoformat(),
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000, "amount": 1e8, "src": "akshare",
    }]))


def _add_pool(conn, symbol: str, level: str = "core", falsify: str = ""):
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, falsify_condition, in_date) "
        "VALUES(?,?,?,?,?)",
        (symbol, level, "白酒", falsify, ASOF.isoformat()),
    )


def _add_card(conn, symbol: str, *, entry="10.0,10.5", stop=9.5, target=12.0,
              status="locked", level="B"):
    conn.execute(
        """INSERT INTO cards(symbol, level, cycle, thesis, falsify, entry_range,
                             stop_loss, target, status, created_at)
           VALUES(?,?,?,?,?,?,?,?,?, datetime('now','localtime'))""",
        (symbol, level, "short", "这是足够长的三句话验证文本", "跌破年线",
         entry, stop, target, status),
    )


def test_schema_has_action_tables():
    p = _tmp_db()
    names = table_names(p)
    assert "daily_actions" in names
    # 2026-09-18：review_lessons（复盘校验库）已随盘后复盘精简删除
    assert "review_lessons" not in names
    conn = connect(p)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_actions)")}
        for c in ("date", "symbol", "verb", "priority", "source", "entry_lo",
                  "entry_hi", "stop_loss", "target", "status", "hint"):
            assert c in cols
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver >= 14
    finally:
        conn.close()


def test_compose_one_row_per_symbol_plan_over_card():
    from invest.actions.compose import compose

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _add_card(conn, "600519", entry="1800,1850", stop=1700, target=2000)
        conn.execute(
            """INSERT INTO trade_plans(symbol, buy_range, stop_loss, take_profit, status)
               VALUES('600519', '1900,1950', 1750, '2100', 'active')"""
        )
        _seed_close(conn, "600519", 1920)
        conn.commit()
        rows = compose(conn, ASOF, for_date=NEXT)
        assert len(rows) == 1
        a = rows[0]
        assert a.symbol == "600519"
        assert a.source == "plan"
        assert a.entry_lo == 1900
        assert a.stop_loss == 1750
        assert a.verb in ("buy", "add", "hold", "wait")
        assert a.priority == 2
    finally:
        conn.close()


def test_compose_stop_broken_is_priority1_sell():
    from invest.actions.compose import compose

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "000001")
        _add_card(conn, "000001", entry="10,11", stop=9.8, target=13)
        _seed_close(conn, "000001", 9.5)
        conn.commit()
        rows = compose(conn, ASOF, for_date=NEXT)
        a = next(x for x in rows if x.symbol == "000001")
        assert a.verb == "sell"
        assert a.priority == 1
        assert a.source == "card"
        assert "建议买入" not in (a.hint or "")
    finally:
        conn.close()


def test_compose_llm_cannot_overwrite_prices_and_drops_nameless_picks():
    from invest.actions.compose import compose

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _add_card(conn, "600519", entry="10,10.5", stop=9.5, target=12)
        _seed_close(conn, "600519", 10.2)
        conn.commit()
        plan = {
            "picks": [
                {"name": "无名", "reason": "追", "plan": "打板"},
                {"name": "某股", "symbol": "300001", "reason": "探索", "plan": "回踩"},
                {"name": "又一只", "symbol": "300002", "reason": "x", "plan": "y"},
                {"name": "第三", "symbol": "300003", "reason": "x", "plan": "y"},
                {"name": "超限", "symbol": "300004", "reason": "x", "plan": "y"},
            ],
            "plans": [{"symbol": "600519", "action": "减仓到半仓"}],
        }
        rows = compose(conn, ASOF, for_date=NEXT, llm_plan=plan)
        by = {r.symbol: r for r in rows}
        assert by["600519"].entry_lo == 10.0
        assert by["600519"].stop_loss == 9.5
        assert by["600519"].verb == "reduce"
        assert "300001" in by and by["300001"].verb == "watch" and by["300001"].priority == 4
        assert "300002" in by and "300003" in by
        assert "300004" not in by  # picks 上限 3
        assert all(r.symbol != "" for r in rows)
        pool_n = conn.execute(
            "SELECT COUNT(*) FROM candidate_pool WHERE symbol IN ('300001','300002')"
        ).fetchone()[0]
        assert pool_n == 0
    finally:
        conn.close()


def test_compose_action_signal_watch_cap_and_evidence():
    from invest.actions.compose import compose
    from invest.signals.persist import persist_signals
    from invest.signals.types import Signal

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _add_card(conn, "600519", entry="10,11", stop=9, target=12)
        _seed_close(conn, "600519", 10.5)
        sigs = [
            Signal(
                id="shrink_extreme", name="极致缩量", session="close", severity="action",
                subject_type="stock", subject="600519", hint="量能收缩",
                horizon="short", layer="watch",
            ),
        ]
        for i in range(10):
            sigs.append(Signal(
                id="high_vol", name="高位放量", session="close", severity="action",
                subject_type="stock", subject=f"00000{i}", hint="放量",
                horizon="short", layer="discovery",
            ))
        persist_signals(conn, sigs, ASOF, "close")
        conn.commit()
        rows = compose(conn, ASOF, for_date=NEXT)
        by = {r.symbol: r for r in rows}
        assert by["600519"].verb != "watch"  # 已有计划行，信号只进 evidence
        assert "shrink_extreme" in str(by["600519"].evidence)
        extras = [r for r in rows if r.source == "signal"]
        assert len(extras) <= 8
        assert all(r.verb == "watch" and r.priority == 3 for r in extras)
    finally:
        conn.close()


def test_persist_and_list_and_refresh_status():
    from invest.actions.compose import compose
    from invest.actions.persist import persist_actions, update_statuses
    from invest.actions.query import list_actions

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _add_card(conn, "600519", entry="10.0,10.5", stop=9.5, target=12)
        _seed_close(conn, "600519", 10.8)
        conn.commit()
        rows = compose(conn, ASOF, for_date=NEXT)
        persist_actions(conn, rows, NEXT)
        got = list_actions(conn, NEXT)
        assert len(got) == 1
        assert got[0]["status"] == "pending"
        n = update_statuses(conn, NEXT, {"600519": 10.2})
        assert n >= 1
        got2 = list_actions(conn, NEXT)
        assert got2[0]["status"] == "triggered"
        assert got2[0]["verb"] == got[0]["verb"]
        assert got2[0]["entry_lo"] == got[0]["entry_lo"]
    finally:
        conn.close()


def test_list_actions_missing_table_empty():
    from invest.actions.query import list_actions

    p = os.path.join(tempfile.gettempdir(), "invest_actions_empty.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    conn = connect(p)
    try:
        conn.execute("CREATE TABLE dummy(x INT)")
        conn.commit()
        assert list_actions(conn, NEXT) == []
    finally:
        conn.close()


def test_format_sorted_by_priority_no_buy_slogan():
    from invest.actions.format import format_actions
    from invest.actions.types import Action

    text = format_actions([
        Action(date="2026-08-24", symbol="300001", verb="watch", priority=4,
               source="llm", hint="探索回踩"),
        Action(date="2026-08-24", symbol="600519", verb="sell", priority=1,
               source="card", stop_loss=95, hint="收盘跌破止损 95"),
        Action(date="2026-08-24", symbol="000001", verb="hold", priority=2,
               source="pool", hint="池内持有"),
    ])
    assert "明日动作" in text or "动作" in text
    pos1 = text.index("600519")
    pos2 = text.index("000001")
    pos3 = text.index("300001")
    assert pos1 < pos2 < pos3
    assert "建议买入" not in text


def test_b1_pick_keeps_triggered_watch():
    from invest.actions.format import pick_b1
    from invest.actions.types import Action

    rows = [
        Action(date="d", symbol="A", verb="hold", priority=2, source="card", status="pending"),
        Action(date="d", symbol="B", verb="watch", priority=4, source="llm", status="triggered"),
        Action(date="d", symbol="C", verb="watch", priority=4, source="llm", status="pending"),
        Action(date="d", symbol="D", verb="sell", priority=1, source="card", status="pending"),
    ]
    picked = pick_b1(rows)
    syms = {r.symbol for r in picked}
    assert syms == {"A", "B", "D"}


def test_position_hint_no_qty():
    from invest.actions.compose import compose

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _add_card(conn, "600519", entry="10,11", stop=9, target=13, level="B")
        _seed_close(conn, "600519", 10.5)
        conn.commit()
        a = compose(conn, ASOF, for_date=NEXT)[0]
        assert "R=" in (a.position_hint or "")
        assert "手" not in (a.position_hint or "")
        assert not any(ch.isdigit() and "股" in (a.position_hint or "") for ch in "")
        assert "100" not in (a.position_hint or "")
    finally:
        conn.close()


def test_expire_active_plans_only_plan_source():
    from invest.actions.persist import expire_active_plans

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute(
            """INSERT INTO viewpoints(source, conclusion, period_tag, status, obj_type, obj)
               VALUES('plan', '{"direction":"旧"}', 'short', 'active', 'market', 'plan')"""
        )
        conn.execute(
            """INSERT INTO viewpoints(source, conclusion, period_tag, status, obj_type, obj)
               VALUES('research', '研究观点', 'short', 'active', 'stock', '600519')"""
        )
        conn.commit()
        expire_active_plans(conn)
        conn.commit()
        st = conn.execute(
            "SELECT status FROM viewpoints WHERE source='plan'"
        ).fetchone()["status"]
        rs = conn.execute(
            "SELECT status FROM viewpoints WHERE source='research'"
        ).fetchone()["status"]
        assert st == "expired"
        assert rs == "active"
    finally:
        conn.close()


def test_d33_and_holdings_and_dashboard_query():
    from dashboard.queries import load_actions
    from invest.actions.compose import compose
    from invest.actions.persist import persist_actions
    from invest.skills.reports.a3_daily import _holdings_text
    from invest.skills.sections.d33_daily_actions import render

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _add_card(conn, "600519", entry="10,11", stop=9.5, target=12)
        _seed_close(conn, "600519", 10.4)
        conn.commit()
        persist_actions(conn, compose(conn, ASOF, for_date=NEXT), NEXT)
    finally:
        conn.close()
    txt = render(p, date=NEXT.isoformat())
    assert "600519" in txt and "动作清单" in txt
    h = _holdings_text(p)
    assert "600519" in h and "止" in h and "入" in h
    df = load_actions(p, NEXT)
    assert not df.empty and "600519" in set(df["symbol"])


def test_b1_brief_keeps_action_table(monkeypatch):
    from invest.actions.compose import compose
    from invest.actions.persist import persist_actions
    from invest.skills.runner import run_structured

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "600519")
        _add_card(conn, "600519", entry="10,11", stop=9.5, target=12)
        _seed_close(conn, "600519", 10.4)
        persist_actions(conn, compose(conn, ASOF, for_date=NEXT), NEXT)
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35}})
    monkeypatch.setattr("invest.data.auction.fetch_batch_quotes", lambda symbols=None: {})
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})
    monkeypatch.setattr("invest.report._live_quotes", lambda *a, **k: ({}, {}))
    # 2026-09-11：按 AGENTS.md「测试全 mock 不连真实网络」拦截报告 LLM，
    # 否则简洁版会拿到真实主线内容（非确定性 + 真实 token 消耗）
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mood_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mainline_llm", lambda *a, **k: {})
    struct = run_structured("b1_intraday", db_path=p, brief=True)
    tables = [s for s in struct["sections"] if s.get("type") == "table"]
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert any(t["title"] == "动作对照" for t in tables)
    assert "日内主线" not in texts or "推演" not in texts

