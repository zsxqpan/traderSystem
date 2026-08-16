"""交易成本模型 + 可交易性校验单元测试。用法: python tests/test_costs.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.discipline.costs import (
    CostParams,
    check_tradable,
    compute_cost,
    fetch_prev_close_and_adv,
    limit_pct,
    record_cost,
    round_lot,
)


def test_compute_cost():
    # 买入 1000 股 @10.0：金额 10000
    params = CostParams()  # 万2.5 / 最低5 / 印花税卖出0.05% / 过户0.001% / 滑点0.1%
    buy = compute_cost(10.0, 1000, "buy", params)
    assert buy.stamp_tax == 0.0  # 买入无印花税
    assert buy.commission >= 5.0  # 10000*0.00025=2.5 -> 最低 5
    assert buy.total > 0
    # 卖出 1000 股 @10.0
    sell = compute_cost(10.0, 1000, "sell", params)
    assert sell.stamp_tax == round(10000 * 0.0005, 2)  # 5.0
    # 小额成交：佣金按最低 5 元
    small = compute_cost(1.0, 100, "buy", params)  # 金额 100
    assert small.commission == 5.0
    print("test_compute_cost OK")


def test_round_lot_and_limit():
    assert round_lot(100) == 100
    assert round_lot(500) == 500
    for bad in (0, -100, 50, 150):
        try:
            round_lot(bad)
            raise AssertionError(f"should reject {bad}")
        except ValueError:
            pass
    assert limit_pct("600519") == 0.10
    assert limit_pct("ST1001") == 0.05
    assert limit_pct("*ST1001") == 0.05
    assert limit_pct("830001") == 0.30  # 北交所
    print("test_round_lot_and_limit OK")


def test_check_tradable():
    p = os.path.join(tempfile.gettempdir(), "invest_costs_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    conn = connect(p)
    # 正常：可交易
    assert check_tradable(conn, "600519", "buy", 10.0, 100, prev_close=9.5, adv=1e8) == []
    # 非整手
    v = check_tradable(conn, "600519", "buy", 10.0, 150, prev_close=9.5, adv=1e8)
    assert any("100 的整数倍" in x for x in v)
    # 超涨跌停
    v2 = check_tradable(conn, "600519", "buy", 12.0, 100, prev_close=10.0, adv=1e8)
    assert any("涨跌停" in x for x in v2)
    # ST 5%
    v3 = check_tradable(conn, "ST1001", "buy", 10.8, 100, prev_close=10.0, adv=1e8)
    assert any("涨跌停" in x for x in v3)
    # ADV 参与率超标
    v4 = check_tradable(conn, "600519", "buy", 10.0, 100000, prev_close=9.5, adv=10000)
    assert any("参与率" in x for x in v4)
    # T+1
    v5 = check_tradable(conn, "600519", "sell", 10.0, 100, prev_close=9.5, adv=1e8,
                        buy_date=os.popen("date /T 2>nul || echo 2026-08-15").read().strip())
    # T+1 用今天日期硬编码判断可能因时区差异失败，改为直接传今天
    import datetime as _dt
    v5 = check_tradable(conn, "600519", "sell", 10.0, 100, prev_close=9.5, adv=1e8,
                        buy_date=_dt.date.today().isoformat())
    assert any("T+1" in x for x in v5)
    # 昨收为 0 / 无 adv：跳过对应校验
    assert check_tradable(conn, "600519", "buy", 10.0, 100, prev_close=0, adv=None) == []
    conn.close()
    print("test_check_tradable OK")


def test_fetch_and_record_cost():
    p = os.path.join(tempfile.gettempdir(), "invest_costs_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    conn = connect(p)
    from invest.data.storage import upsert_df
    import pandas as pd
    upsert_df(conn, "daily_bars", pd.DataFrame([
        {"symbol": "600519", "date": "2026-08-13", "close": 10.0, "amount": 1e8, "src": "akshare"},
        {"symbol": "600519", "date": "2026-08-12", "close": 9.8, "amount": 9e7, "src": "akshare"},
        {"symbol": "600519", "date": "2026-08-11", "close": 9.5, "amount": 8e7, "src": "akshare"},
    ]))
    prev_close, adv = fetch_prev_close_and_adv(conn, "600519")
    assert prev_close == 10.0
    assert adv is not None and adv > 0
    # 记录一笔并附成本
    from invest.discipline import plans, pool
    pool.add_to_pool(conn, "600519")
    plan = plans.create_plan(conn, "600519", stop_loss=8.0, buy_range="9.0,11.0")
    from invest.discipline import records
    rec = records.record_trade(conn, plan["plan_id"], "buy", 10.0, 100)
    cost = compute_cost(10.0, 100, "buy")
    record_cost(conn, rec["record_id"], cost)
    note = conn.execute("SELECT deviation_note FROM trade_records WHERE id=?",
                        (rec["record_id"],)).fetchone()["deviation_note"]
    assert "成本[" in note and "佣金" in note
    conn.close()
    print("test_fetch_and_record_cost OK")




def test_liquidity_breach_freeze():
    p = os.path.join(tempfile.gettempdir(), "invest_costs_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    conn = connect(p)
    from invest.discipline.costs import is_frozen, mark_liquidity_breach
    from invest.discipline import risk, pool
    pool.add_to_pool(conn, "600519", level="core", reason="测试")
    mark_liquidity_breach(conn, "600519", "跌停无法卖出")
    assert is_frozen(conn, "600519") is True
    assert is_frozen(conn, "000001") is False
    # check_position 拒绝冻结标的
    v = risk.check_position(conn, proposed=0.05, data_ok=True, symbol="600519")
    assert any("流动性冻结" in x for x in v)
    # 未冻结标的不受影响
    v2 = risk.check_position(conn, proposed=0.05, data_ok=True, symbol="000001")
    assert not any("流动性冻结" in x for x in v2)
    conn.close()
    print("test_liquidity_breach_freeze OK")


if __name__ == "__main__":
    test_compute_cost()
    test_round_lot_and_limit()
    test_check_tradable()
    test_fetch_and_record_cost()
    test_liquidity_breach_freeze()
    print("\nALL COSTS TESTS PASSED")
