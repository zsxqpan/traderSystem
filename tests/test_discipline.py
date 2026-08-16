"""执行纪律层单元测试。用法: python tests/test_discipline.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.discipline import pool, plans, rating, records, risk


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_discipline_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_pool_capacity():
    p = _tmp_db()
    conn = connect(p)
    for i in range(10):
        pool.add_to_pool(conn, f"C{i:03d}", level="core")
    for i in range(9):
        pool.add_to_pool(conn, f"S{i:03d}")
    assert len(pool.list_pool(conn)) == 19
    try:
        pool.add_to_pool(conn, "C999", level="core")
        raise AssertionError("should reject 11th core")
    except ValueError as e:
        assert "核心关注已满" in str(e)
    pool.add_to_pool(conn, "S999")
    assert len(pool.list_pool(conn)) == 20
    try:
        pool.add_to_pool(conn, "S998")
        raise AssertionError("should reject 21st")
    except ValueError as e:
        assert "候选池已满" in str(e)
    pool.remove_from_pool(conn, "C000")
    assert len(pool.list_pool(conn)) == 19
    conn.close()
    print("test_pool_capacity OK")


def test_rating_position():
    p = _tmp_db()
    conn = connect(p)
    rating.set_rating(conn, "macro", "宽松")
    rating.set_rating(conn, "market", "进攻")
    assert rating.get_rating(conn, "macro")["value"] == "宽松"
    assert rating.get_position_limit(conn) == 0.4  # 校准后
    rating.set_rating(conn, "macro", "收紧")
    rating.set_rating(conn, "market", "防守")
    assert rating.get_position_limit(conn) == 0.05
    conn.close()
    print("test_rating_position OK")


def test_plan_validations():
    p = _tmp_db()
    conn = connect(p)
    try:
        plans.create_plan(conn, "X1", stop_loss=9.0)
        raise AssertionError("should require pool membership")
    except ValueError as e:
        assert "候选池" in str(e)
    pool.add_to_pool(conn, "X1")
    try:
        plans.create_plan(conn, "X1")
        raise AssertionError("should require stop loss")
    except ValueError as e:
        assert "止损" in str(e)
    rating.set_rating(conn, "macro", "收紧")
    rating.set_rating(conn, "market", "防守")
    try:
        plans.create_plan(conn, "X1", stop_loss=9.0, target_position=0.5)
        raise AssertionError("should exceed position cap")
    except ValueError as e:
        assert "仓位上限" in str(e)
    plan = plans.create_plan(conn, "X1", stop_loss=9.0, target_position=0.05)
    assert plan["status"] == "active"
    assert len(plans.list_active_plans(conn)) == 1
    conn.close()
    print("test_plan_validations OK")


def test_risk_checks():
    p = _tmp_db()
    conn = connect(p)
    rating.set_rating(conn, "macro", "宽松")
    rating.set_rating(conn, "market", "进攻")
    pool.add_to_pool(conn, "X1")
    plan = plans.create_plan(conn, "X1", stop_loss=9.0, target_position=0.05)
    pid = plan["plan_id"]
    violations = risk.check_position(conn, proposed=0.20, total_position=0.60, industry_position=0.20)
    assert any("单票" in v for v in violations)
    assert any("行业" in v for v in violations)
    assert risk.check_stop_loss({"stop_loss": 9.0}, 8.9) is True
    assert risk.check_stop_loss({"stop_loss": 9.0}, 9.1) is False
    assert risk.check_drawdown(100.0, 84.0) is True
    assert risk.check_drawdown(100.0, 90.0) is False
    conn.close()
    print("test_risk_checks OK")


def test_records_deviation():
    p = _tmp_db()
    conn = connect(p)
    pool.add_to_pool(conn, "X1")
    plan = plans.create_plan(conn, "X1", stop_loss=9.0, buy_range="10.0,10.5")
    pid = plan["plan_id"]
    r1 = records.record_trade(conn, pid, "buy", 10.2, 1000)
    assert r1["actual_vs_plan"] == "in_range"
    r2 = records.record_trade(conn, pid, "buy", 11.0, 500)
    assert r2["actual_vs_plan"] == "above_range"
    r3 = records.record_trade(conn, pid, "buy", 8.9, 100)
    assert "触发止损" in r3["deviation_note"]
    assert len(records.list_records(conn, pid)) == 3
    conn.close()
    print("test_records_deviation OK")




def test_pool_industry():
    p = _tmp_db()
    conn = connect(p)
    r = pool.add_to_pool(conn, "600519", level="core", industry="白酒")
    assert r["industry"] == "白酒"
    row = conn.execute("SELECT industry FROM candidate_pool WHERE symbol='600519'").fetchone()
    assert row["industry"] == "白酒"
    conn.close()
    print("test_pool_industry OK")



def test_risk_data_guard_degrade():
    import datetime as _dt
    p = _tmp_db()
    conn = connect(p)
    rating.set_rating(conn, "macro", "宽松")
    rating.set_rating(conn, "market", "进攻")
    # 无实时留痕 + 无日线 -> data_guard 应报实时行情失效
    v = risk.data_guard(conn, db_path=p)
    assert any("实时行情失效" in x for x in v)
    # data_ok=False -> check_position 强制禁止新开仓
    violations = risk.check_position(conn, proposed=0.05, data_ok=False)
    assert any("数据失效" in x for x in violations)
    # data_ok=True + 仓位合规 -> 无违规
    ok_v = risk.check_position(conn, proposed=0.05, data_ok=True)
    assert not any("数据失效" in x for x in ok_v)
    # 日线陈旧 -> 报日线数据陈旧
    conn.execute(
        "INSERT INTO daily_bars(symbol, date, close, src) VALUES('X1', ?, 10.0, 'akshare')",
        ((_dt.date.today() - _dt.timedelta(days=30)).isoformat(),),
    )
    conn.commit()
    v2 = risk.data_guard(conn, db_path=p)
    assert any("日线数据陈旧" in x for x in v2)
    conn.close()
    print("test_risk_data_guard_degrade OK")


if __name__ == "__main__":
    test_pool_capacity()
    test_rating_position()
    test_plan_validations()
    test_risk_checks()
    test_risk_data_guard_degrade()
    test_records_deviation()
    test_pool_industry()
    print("\nALL DISCIPLINE TESTS PASSED")