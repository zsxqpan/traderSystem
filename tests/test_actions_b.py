"""阶段 B/C：观察名单、监控并轨、动作标记、digest。临时库，不联网。"""
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
PREV = dt.date(2026, 8, 20)


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_actions_b_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_schema_has_watch_items():
    p = _tmp_db()
    assert "watch_items" in table_names(p)
    conn = connect(p)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(watch_items)")}
        for c in ("symbol", "source", "status", "in_date", "expire_date"):
            assert c in cols
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver >= 15
    finally:
        conn.close()


def test_watch_add_dismiss_promote_not_auto_core():
    from invest.actions.watch import add_watch, dismiss_watch, list_watch, promote_watch

    p = _tmp_db()
    conn = connect(p)
    try:
        add_watch(conn, "300001", source="user", reason="盘后想看")
        rows = list_watch(conn)
        assert len(rows) == 1 and rows[0]["symbol"] == "300001" and rows[0]["status"] == "open"
        n = conn.execute("SELECT COUNT(*) FROM candidate_pool").fetchone()[0]
        assert n == 0
        promote_watch(conn, "300001", level="track")
        assert list_watch(conn) == []
        promoted = list_watch(conn, status="promoted")
        assert promoted and promoted[0]["status"] == "promoted"
        pool = conn.execute(
            "SELECT level FROM candidate_pool WHERE symbol='300001' AND out_date IS NULL"
        ).fetchone()
        assert pool["level"] == "track"
        add_watch(conn, "300002", source="user")
        dismiss_watch(conn, "300002")
        assert list_watch(conn) == []
        dec = [r["decision"] for r in conn.execute("SELECT decision FROM candidate_decisions")]
        assert "add" in dec and "reject" in dec
    finally:
        conn.close()


def test_watch_from_two_signal_days():
    from invest.actions.persist import persist_actions
    from invest.actions.types import Action
    from invest.actions.watch import list_watch, maybe_open_from_actions

    p = _tmp_db()
    conn = connect(p)
    try:
        persist_actions(conn, [Action(
            date=PREV.isoformat(), symbol="000001", verb="watch",
            priority=3, source="signal", hint="放量",
        )], PREV)
        persist_actions(conn, [Action(
            date=ASOF.isoformat(), symbol="000001", verb="watch",
            priority=3, source="signal", hint="再放量",
        )], ASOF)
        maybe_open_from_actions(conn, ASOF)
        rows = list_watch(conn)
        assert any(r["symbol"] == "000001" and r["source"] == "signal" for r in rows)
        n = conn.execute("SELECT COUNT(*) FROM candidate_pool").fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_mark_action_done():
    from invest.actions.persist import mark_action, persist_actions
    from invest.actions.query import list_actions
    from invest.actions.types import Action

    p = _tmp_db()
    conn = connect(p)
    try:
        persist_actions(conn, [Action(
            date=ASOF.isoformat(), symbol="600519", verb="hold",
            priority=2, source="card",
        )], ASOF)
        mark_action(conn, ASOF, "600519", "done")
        assert list_actions(conn, ASOF)[0]["status"] == "done"
        try:
            mark_action(conn, ASOF, "600519", "pending")
            raise AssertionError("should reject")
        except ValueError:
            pass
    finally:
        conn.close()


def test_monitor_reads_cards_plan_wins():
    from invest.monitor import check_position_falsify

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute(
            """INSERT INTO cards(symbol, level, thesis, stop_loss, status)
               VALUES('000002', 'B', '这是足够长的三句话验证文本', 8.0, 'locked')"""
        )
        conn.execute(
            """INSERT INTO cards(symbol, level, thesis, stop_loss, status)
               VALUES('000001', 'B', '这是足够长的三句话验证文本', 9.0, 'locked')"""
        )
        conn.execute(
            """INSERT INTO trade_plans(symbol, stop_loss, status)
               VALUES('000001', 10.0, 'active')"""
        )
        conn.commit()
    finally:
        conn.close()
    alerts = check_position_falsify(p, {"000001": 9.5, "000002": 7.5})
    by = {a["symbol"]: a for a in alerts}
    assert by["000002"]["kind"] == "stop_loss"
    assert "卡片" in by["000002"]["msg"]
    assert by["000001"]["kind"] == "stop_loss"
    assert "计划#" in by["000001"]["msg"]
    assert sum(1 for a in alerts if a["symbol"] == "000001") == 1
    assert check_position_falsify(p, {"000002": 9.0}) == []


def test_b1_core_includes_locked_card():
    from invest.skills.reports.b1_intraday import _core_quotes

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute(
            """INSERT INTO cards(symbol, level, thesis, stop_loss, status)
               VALUES('000333', 'A', '这是足够长的三句话验证文本', 40.0, 'locked')"""
        )
        upsert_df(conn, "daily_bars", pd.DataFrame([{
            "symbol": "000333", "date": ASOF.isoformat(),
            "open": 50, "high": 50, "low": 50, "close": 50,
            "volume": 1, "amount": 1, "src": "akshare",
        }]))
        conn.commit()
    finally:
        conn.close()
    rows, _lines, _rs = _core_quotes(p)
    assert any(r[0] == "000333" for r in rows)


def test_watch_expire_due():
    from invest.actions.watch import add_watch, expire_due, list_watch

    p = _tmp_db()
    conn = connect(p)
    try:
        add_watch(conn, "000001", source="user", asof=dt.date(2026, 8, 3))
        assert expire_due(conn, dt.date(2026, 8, 9)) == 0
        assert list_watch(conn)
        assert expire_due(conn, dt.date(2026, 8, 10)) == 1
        assert list_watch(conn) == []
        assert list_watch(conn, status="expired")
    finally:
        conn.close()


def test_load_watch_query():
    from dashboard.queries import load_watch
    from invest.actions.watch import add_watch

    p = _tmp_db()
    conn = connect(p)
    try:
        add_watch(conn, "000001", source="user", reason="看量", asof=ASOF)
    finally:
        conn.close()
    df = load_watch(p)
    assert not df.empty and "000001" in set(df["symbol"])


# 2026-09-18：动作 digest（10:00/13:30 推送）与 invest/actions/digest.py 已按需求删除，
# 原 test_notify_digest_sends_priority_one / test_digest_priority_and_limit 一并移除。
