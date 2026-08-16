"""凯利仓位决策单元测试。用法: python tests/test_kelly.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.discipline import kelly


def test_wilson_lower():
    # 大样本：胜率 60%，下界接近但略低于 0.6
    wl = kelly.wilson_lower(1000, 600)
    assert 0.56 < wl < 0.60
    # 小样本：胜率 60%（3/5），下界显著低于 0.6（保守）
    wl_small = kelly.wilson_lower(5, 3)
    assert 0.0 < wl_small < 0.5
    # n=0 -> 0
    assert kelly.wilson_lower(0, 0) == 0.0
    # 全胜小样本下界仍 > 0 但不高
    wl_all = kelly.wilson_lower(5, 5)
    assert 0.4 < wl_all < 1.0
    print("test_wilson_lower OK")


def test_kelly_fraction():
    # p=0.6, b=1（盈亏比 1:1）-> f* = 0.6 - 0.4 = 0.2
    assert abs(kelly.kelly_fraction(0.6, 1.0) - 0.2) < 1e-9
    # p=0.5, b=1 -> 0（无优势不下注）
    assert kelly.kelly_fraction(0.5, 1.0) == 0.0
    # p=0.3, b=2 -> 0.3 - 0.7/2 = -0.05 -> 0
    assert kelly.kelly_fraction(0.3, 2.0) == 0.0
    # odds<=0 -> 0
    assert kelly.kelly_fraction(0.8, 0) == 0.0
    print("test_kelly_fraction OK")


def test_kelly_capped():
    # 标准 0.2 × 1/6 = 0.0333
    capped = kelly.kelly_capped(0.6, 1.0)
    assert abs(capped - 0.2 / 6) < 1e-9
    print("test_kelly_capped OK")


def test_kelly_decision():
    # 样本不足 -> 回退固定风险
    d1 = kelly.kelly_decision(10, 6, 1.0)
    assert d1["enabled"] is False
    assert d1["fraction"] == 0.10
    assert "样本不足" in d1["reason"]
    # 样本够但胜率极低 -> Wilson 下界 <=0 -> 回退
    d2 = kelly.kelly_decision(50, 3, 1.0)  # 6% 胜率
    assert d2["enabled"] is False
    assert "Wilson" in d2["reason"]
    # 样本够 + 高胜率 -> 启用凯利
    d3 = kelly.kelly_decision(50, 40, 1.5)
    assert d3["enabled"] is True
    assert d3["fraction"] > 0
    assert d3["fraction"] < d3["kelly_raw"]  # 1/6 系数后小于原始
    # 小样本高胜率（3/5=60%）也不启用（<20 笔）
    d4 = kelly.kelly_decision(5, 5, 1.0)
    assert d4["enabled"] is False
    print("test_kelly_decision OK")


def test_evaluate_grid():
    p = os.path.join(tempfile.gettempdir(), "invest_kelly_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    conn = connect(p)
    from invest.discipline import pool, plans, records
    pool.add_to_pool(conn, "X1", level="core")
    plan = plans.create_plan(conn, "X1", stop_loss=9.0, buy_range="10.0,10.5")
    pid = plan["plan_id"]
    # 25 笔交易：20 胜 5 负（盈亏比 ~1:1）
    for i in range(20):
        records.record_trade(conn, pid, "buy", 10.0, 100)
        conn.execute("UPDATE trade_records SET pnl=? WHERE id=last_insert_rowid()", (1.0,))
    for i in range(5):
        records.record_trade(conn, pid, "sell", 10.0, 100)
        conn.execute("UPDATE trade_records SET pnl=? WHERE id=last_insert_rowid()", (-1.0,))
    conn.commit()
    r = kelly.evaluate_grid(conn, cycle="short", level="core")
    assert r["n"] == 25
    assert r["wins"] == 20
    assert r["decision"]["enabled"] is True
    assert r["key"] == "short|core|"
    # 空池格子
    r2 = kelly.evaluate_grid(conn, cycle="short", level="track")
    assert r2["n"] == 0 and r2["decision"]["enabled"] is False
    conn.close()
    print("test_evaluate_grid OK")


if __name__ == "__main__":
    test_wilson_lower()
    test_kelly_fraction()
    test_kelly_capped()
    test_kelly_decision()
    test_evaluate_grid()
    print("\nALL KELLY TESTS PASSED")
