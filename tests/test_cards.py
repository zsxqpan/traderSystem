"""机会卡片与对象池硬门槛单元测试。用法: python tests/test_cards.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.discipline import pool
from invest.discipline.cards import (
    CARD_LIMIT,
    compute_rr,
    create_card,
    evict_weakest,
    list_cards,
    lock_card,
    transition,
    validate_card,
    weakest_card,
)
from invest.discipline.pool_rules import (
    check_and_add,
    freeze_symbol,
    hard_gate_check,
    is_frozen_symbol,
    list_l2_industries,
    unfreeze_symbol,
)
from invest.data.storage import upsert_df

import pandas as pd


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_cards_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed_daily(conn, symbol, days=80, adv=1e8):
    """造 80 天日线（满足上市 60 日 + ADV 门槛）。"""
    import datetime as dt
    rows = []
    d = dt.date(2026, 1, 1)
    for i in range(days):
        rows.append({"symbol": symbol, "date": (d + dt.timedelta(days=i)).isoformat(),
                     "close": 10.0 + i * 0.01, "amount": adv, "src": "akshare"})
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))


def test_hard_gate():
    p = _tmp_db()
    conn = connect(p)
    # ST 直接否决
    v = hard_gate_check(conn, "ST1001")
    assert any("ST" in x for x in v)
    # 有足够日线 + 高 ADV -> 通过
    _seed_daily(conn, "600519")
    assert hard_gate_check(conn, "600519") == []
    # ADV 不足
    conn.execute("DELETE FROM daily_bars")
    _seed_daily(conn, "600519", adv=1e6)  # 100 万 < 5000 万
    v2 = hard_gate_check(conn, "600519")
    assert any("成交额" in x for x in v2)
    conn.close()
    print("test_hard_gate OK")


def test_check_and_add_reject_logged():
    p = _tmp_db()
    conn = connect(p)
    from invest.data.pit import list_decisions
    try:
        check_and_add(conn, "ST9999", level="core")
        raise AssertionError("ST 应被拒绝")
    except ValueError:
        pass
    # 否决已留痕（防选择偏差）
    d = list_decisions(conn, symbol="ST9999")
    assert any(x["decision"] == "reject" for x in d)
    # 合格标的可入池
    _seed_daily(conn, "600519")
    r = check_and_add(conn, "600519", level="core", industry="白酒")
    assert r["level"] == "core"
    conn.close()
    print("test_check_and_add_reject_logged OK")


def test_freeze_flow():
    p = _tmp_db()
    conn = connect(p)
    assert is_frozen_symbol(conn, "600519") is False
    freeze_symbol(conn, "600519", reason="停牌核查")
    assert is_frozen_symbol(conn, "600519") is True
    n = unfreeze_symbol(conn, "600519")
    assert n == 1
    assert is_frozen_symbol(conn, "600519") is False
    conn.close()
    print("test_freeze_flow OK")


def test_create_card_validations():
    p = _tmp_db()
    conn = connect(p)
    _seed_daily(conn, "600519")
    check_and_add(conn, "600519", level="core", industry="白酒")
    # thesis 太短 -> 拒绝
    try:
        create_card(conn, "600519", thesis="看多")
        raise AssertionError("短 thesis 应拒绝")
    except ValueError as e:
        assert "三句话" in str(e)
    # 不在候选池 -> 拒绝
    try:
        create_card(conn, "000001", thesis="这是一个足够长的投资逻辑说明文本")
        raise AssertionError("不在候选池应拒绝")
    except ValueError as e:
        assert "候选池" in str(e)
    # 正常建卡
    card = create_card(
        conn, "600519", level="A", cycle="short",
        thesis="白酒景气回升，估值处于历史低位，动销数据验证中",
        falsify="月度动销转负", entry_range="1500,1550",
        stop_loss=1400.0, target=1800.0,
    )
    assert card["status"] == "candidate"
    conn.close()
    print("test_create_card_validations OK")


def test_card_lifecycle():
    p = _tmp_db()
    conn = connect(p)
    _seed_daily(conn, "600519")
    check_and_add(conn, "600519", level="core", industry="白酒")
    card = create_card(
        conn, "600519", level="A", cycle="short",
        thesis="白酒景气回升，估值处于历史低位，动销数据验证中",
        falsify="月度动销转负", entry_range="1500,1550",
        stop_loss=1400.0, target=1800.0,
    )
    cid = card["card_id"]
    # 完整性校验通过
    assert validate_card(conn, cid) == []
    # 锁卡
    r = lock_card(conn, cid)
    assert r["status"] == "locked"
    # 非法迁移：locked -> candidate 不允许
    try:
        transition(conn, cid, "candidate")
        raise AssertionError("locked→candidate 非法")
    except ValueError:
        pass
    # 合法迁移：locked -> review -> downgraded
    transition(conn, cid, "review", note="周期漂移")
    transition(conn, cid, "downgraded", note="证伪")
    assert list_cards(conn, status="downgraded")[0]["status"] == "downgraded"
    conn.close()
    print("test_card_lifecycle OK")


def test_card_capacity():
    p = _tmp_db()
    conn = connect(p)
    # 候选池上限 20：入池 20 个标的
    for i in range(20):
        sym = f"C{i:04d}"
        _seed_daily(conn, sym)
        check_and_add(conn, sym, level="track")
    # 建 20 张卡
    for i in range(20):
        sym = f"C{i:04d}"
        create_card(conn, sym, level="B", thesis=f"标的 {sym} 投资逻辑说明文本，验证充分",
                    falsify="x", entry_range="9,11", stop_loss=8.0, target=12.0)
    assert len(list_cards(conn)) == 20
    # 第 21 张（同一标的再建一张）-> 拒绝（容量满）
    try:
        create_card(conn, "C0000", level="B", thesis="标的 C0000 投资逻辑说明文本，验证充分",
                    falsify="x", entry_range="9,11", stop_loss=8.0, target=12.0)
        raise AssertionError("容量满应拒绝")
    except ValueError as e:
        assert "已满" in str(e)
    # 最弱卡片可被淘汰
    w = weakest_card(conn)
    assert w is not None
    evict_weakest(conn, reason="容量淘汰")
    cards = list_cards(conn)
    active = [c for c in cards if c["status"] != "downgraded"]
    assert len(active) == 19  # 1 张已 downgraded
    conn.close()
    print("test_card_capacity OK")


def test_compute_rr():
    assert compute_rr(1500.0, 1400.0, 1800.0) == 3.0  # (300)/(100)
    assert compute_rr(10.0, 9.0, 11.0) == 1.0
    # 目标 < 入场 -> 赔率 0
    assert compute_rr(10.0, 9.0, 9.5) == 0.0
    # 含成本
    assert compute_rr(10.0, 9.0, 11.0, cost=0.1) < 1.0
    print("test_compute_rr OK")


if __name__ == "__main__":
    test_hard_gate()
    test_check_and_add_reject_logged()
    test_freeze_flow()
    test_create_card_validations()
    test_card_lifecycle()
    test_card_capacity()
    test_compute_rr()
    print("\nALL CARDS TESTS PASSED")
