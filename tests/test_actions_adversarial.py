"""动作闭环对抗审查：不自动入池/下单，以及会上线的合成/digest 真 bug。

临时库，不联网，不读 data/invest.db。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.data.storage import upsert_df
from invest.db import connect, init_db

ASOF = dt.date(2026, 8, 21)  # 周五
NEXT = dt.date(2026, 8, 24)  # 周一


def _tmp_db():
    fd, p = tempfile.mkstemp(suffix="_actions_adv.db")
    os.close(fd)
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


def _add_pool(conn, symbol: str, level: str = "core"):
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, falsify_condition, in_date) "
        "VALUES(?,?,?,?,?)",
        (symbol, level, "白酒", "", ASOF.isoformat()),
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


def test_llm_slogan_and_negation_do_not_flip_hold_to_buy():
    """「建议买入」是口号；否定句不能把持有打成买。"""
    from invest.actions.compose import compose

    p = _tmp_db()
    conn = connect(p)
    try:
        for i, action in enumerate((
            "建议买入",
            "不建议买入，继续持有",
            "继续持有，不买入",
            "持有，暂不加仓",
        )):
            sym = f"60051{i}"
            _add_pool(conn, sym)
            _add_card(conn, sym, entry="10,11", stop=9.5, target=13)
            _seed_close(conn, sym, 12.0)
            rows = compose(conn, ASOF, for_date=NEXT, llm_plan={
                "plans": [{"symbol": sym, "action": action}],
            })
            a = next(x for x in rows if x.symbol == sym)
            assert a.verb != "buy", action
            assert a.verb != "add", action
            assert a.priority != 1
            assert a.entry_lo == 10.0 and a.stop_loss == 9.5
            assert "建议买入" not in (a.hint or "")
        n = conn.execute(
            "SELECT COUNT(*) FROM candidate_pool WHERE symbol LIKE '300%'"
        ).fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_compose_llm_cannot_change_priority1_sell():
    from invest.actions.compose import compose

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "000001")
        _add_card(conn, "000001", entry="10,11", stop=9.8, target=13)
        _seed_close(conn, "000001", 9.5)
        conn.commit()
        rows = compose(conn, ASOF, for_date=NEXT, llm_plan={
            "plans": [{"symbol": "000001", "action": "建议买入，继续持有"}],
        })
        a = next(x for x in rows if x.symbol == "000001")
        assert a.verb == "sell" and a.priority == 1
        assert a.stop_loss == 9.8
        assert "建议买入" not in (a.hint or "")
    finally:
        conn.close()


def test_compose_uses_asof_close_not_later_bar():
    """破止损必须用 asof 收盘，不能拿之后交易日的 K 线。"""
    from invest.actions.compose import compose

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "000001")
        _add_card(conn, "000001", entry="10,11", stop=9.8, target=13)
        _seed_close(conn, "000001", 10.2, ASOF)
        _seed_close(conn, "000001", 9.0, NEXT)
        conn.commit()
        rows = compose(conn, ASOF, for_date=NEXT)
        a = next(x for x in rows if x.symbol == "000001")
        assert a.verb != "sell"
        assert a.priority == 2
        assert a.stop_loss == 9.8
    finally:
        conn.close()


def test_zero_close_is_not_stop_break():
    from invest.actions.compose import compose

    p = _tmp_db()
    conn = connect(p)
    try:
        _add_pool(conn, "000001")
        _add_card(conn, "000001", entry="10,11", stop=9.8, target=13)
        _seed_close(conn, "000001", 0.0)
        conn.commit()
        a = compose(conn, ASOF, for_date=NEXT)[0]
        assert a.verb != "sell"
        assert a.priority != 1
    finally:
        conn.close()


def test_update_statuses_ignores_non_positive_price_and_keeps_verb():
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
        persist_actions(conn, compose(conn, ASOF, for_date=NEXT), NEXT)
        before = list_actions(conn, NEXT)[0]
        n = update_statuses(conn, NEXT, {"600519": 0.0, "000001": -1})
        after = list_actions(conn, NEXT)[0]
        assert n == 0
        assert after["status"] == "pending"
        assert after["verb"] == before["verb"]
        assert after["entry_lo"] == before["entry_lo"]
        assert after["stop_loss"] == before["stop_loss"]
    finally:
        conn.close()


def test_close_session_signals_win_the_cap_over_auction():
    """盘后合成应优先昨夜 close，不能被当日 auction 占满 8 席。"""
    from invest.actions.compose import compose
    from invest.signals.persist import persist_signals
    from invest.signals.types import Signal

    p = _tmp_db()
    conn = connect(p)
    try:
        auction = [
            Signal(
                id="auction_keep_vol", name="竞价保量", session="auction",
                severity="action", subject_type="stock", subject=f"00000{i}",
                hint="竞价保量", horizon="short", layer="discovery",
            )
            for i in range(8)
        ]
        close = [
            Signal(
                id="shrink_extreme", name="极致缩量", session="close",
                severity="action", subject_type="stock", subject=sym,
                hint="量能收缩", horizon="short", layer="discovery",
            )
            for sym in ("600001", "600002")
        ]
        persist_signals(conn, auction, ASOF, "auction")
        persist_signals(conn, close, ASOF, "close")
        conn.commit()
        rows = compose(conn, ASOF, for_date=NEXT)
        extras = [r for r in rows if r.source == "signal"]
        assert len(extras) <= 8
        syms = {r.symbol for r in extras}
        assert "600001" in syms and "600002" in syms
        assert all(r.verb == "watch" and r.priority == 3 for r in extras)
        n = conn.execute("SELECT COUNT(*) FROM candidate_pool").fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_format_digest_multiline_hint_stays_within_15_lines():
    from invest.actions.digest import format_digest
    from invest.actions.types import Action

    rows = [
        Action(
            date="d", symbol=f"{i:06d}", verb="sell", priority=1,
            source="card", status="pending",
            hint="第一行\n建议买入\n第三行还很长",
        )
        for i in range(1, 10)
    ]
    text = format_digest(rows)
    assert text
    assert len(text.splitlines()) <= 15
    assert "建议买入" not in text
    assert "\n建议" not in text


def test_format_digest_empty_and_no_stale_unbroken_stop():
    from invest.actions.digest import format_digest
    from invest.actions.types import Action

    assert format_digest([]) == ""
    assert format_digest([
        Action(date="d", symbol="600519", verb="watch", priority=4,
               source="llm", status="pending", hint="探索"),
    ]) == ""

    text = format_digest([
        Action(
            date="d", symbol="600519", verb="hold", priority=2,
            source="card", status="triggered", stop_loss=9.5,
            hint="600519 止损 9.5，收盘 10.8 未破",
            evidence={"last": 9.4},
        ),
    ])
    assert "600519" in text
    assert "未破" not in text
    assert "建议买入" not in text
    assert len(text.splitlines()) <= 15


def test_maybe_open_one_day_does_not_enter_watch_or_pool():
    from invest.actions.persist import persist_actions
    from invest.actions.types import Action
    from invest.actions.watch import list_watch, maybe_open_from_actions

    p = _tmp_db()
    conn = connect(p)
    try:
        persist_actions(conn, [Action(
            date=ASOF.isoformat(), symbol="000001", verb="watch",
            priority=3, source="signal", hint="一天而已",
        )], ASOF)
        assert maybe_open_from_actions(conn, ASOF) == []
        assert list_watch(conn) == []
        assert conn.execute("SELECT COUNT(*) FROM candidate_pool").fetchone()[0] == 0
    finally:
        conn.close()
