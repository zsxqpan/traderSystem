"""kill-gate 击杀门禁单元测试。用法: python tests/test_kill_gate.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.discipline.kill_gate import (
    _max_consecutive_losses,
    _max_drawdown,
    _profit_factor,
    kill_gate_check,
)


def _trades(pnls):
    """构造逐笔交易（按 created_at 排序）。"""
    return [
        {"created_at": f"2026-01-{i+1:02d} 10:00:00", "pnl": float(p)}
        for i, p in enumerate(pnls)
    ]


def test_metrics_primitives():
    # 最大回撤：10 连盈后 50% 回撤
    pnls = [1.0] * 10 + [-5.0]
    assert abs(_max_drawdown(pnls) - 0.5) < 1e-6
    # 连亏
    assert _max_consecutive_losses([1, -1, -2, -3, 1, -1]) == 3
    # 盈利因子
    assert abs(_profit_factor([2.0, 3.0, -2.0]) - 2.5) < 1e-6
    assert _profit_factor([1.0, 2.0]) == float("inf")
    assert _profit_factor([-1.0]) == 0.0
    print("test_metrics_primitives OK")


def test_kill_gate_insufficient_samples():
    """样本不足直接击杀。"""
    r = kill_gate_check(trades=_trades([1.0] * 5))
    assert r["passed"] is False
    assert any("样本不足" in k for k in r["killed_reasons"])
    print("test_kill_gate_insufficient_samples OK")


def test_kill_gate_good_strategy():
    """健康策略通过：20 笔、高胜率、低回撤、盈利因子>1.2。"""
    pnls = [1.0] * 14 + [-0.3] * 6  # 70% 胜率，回撤约 15%<20%
    r = kill_gate_check(trades=_trades(pnls))
    assert r["passed"] is True, r["killed_reasons"]
    print("test_kill_gate_good_strategy OK")


def test_kill_gate_drawdown_kill():
    """回撤超限击杀。"""
    # 20 笔：先小盈后大亏，回撤 > 20%
    pnls = [0.5] * 10 + [-6.0] * 10
    r = kill_gate_check(trades=_trades(pnls))
    assert r["passed"] is False
    assert any("回撤" in k for k in r["killed_reasons"])
    print("test_kill_gate_drawdown_kill OK")


def test_kill_gate_consecutive_losses_kill():
    """连亏超限击杀。"""
    pnls = [1.0] * 5 + [-1.0] * 7 + [1.0] * 8  # 连亏 7 > 6
    r = kill_gate_check(trades=_trades(pnls))
    assert r["passed"] is False
    assert any("连亏" in k for k in r["killed_reasons"])
    print("test_kill_gate_consecutive_losses_kill OK")


def test_kill_gate_from_db():
    """从 trade_records 读取真实记录。"""
    p = os.path.join(tempfile.gettempdir(), "invest_killgate_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    conn = connect(p)
    for i in range(20):
        pnl = 1.0 if i < 14 else -0.3
        conn.execute(
            "INSERT INTO trade_records(plan_id, action, price, qty, pnl, created_at) "
            "VALUES(1,'buy',10,100,?,datetime('now','localtime','-%d days'))" % (20 - i),
            (pnl,),
        )
    conn.commit()
    r = kill_gate_check(conn=conn)
    assert r["passed"] is True
    assert r["metrics"]["n_trades"] == 20
    conn.close()
    print("test_kill_gate_from_db OK")


def test_kill_gate_in_bcs():
    """kill-gate 已挂入 BCS 评估。"""
    from invest.review.bcs import full_assessment
    p = os.path.join(tempfile.gettempdir(), "invest_killgate_bcs.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    conn = connect(p)
    # 无交易样本 → kill-gate 不通过（样本不足）
    r = full_assessment(conn)
    assert "kill_gate" in r
    assert r["kill_gate"]["passed"] is False  # 无样本 → 击杀
    assert r["overall"] == "不通过"
    conn.close()
    print("test_kill_gate_in_bcs OK")


if __name__ == "__main__":
    test_metrics_primitives()
    test_kill_gate_insufficient_samples()
    test_kill_gate_good_strategy()
    test_kill_gate_drawdown_kill()
    test_kill_gate_consecutive_losses_kill()
    test_kill_gate_from_db()
    test_kill_gate_in_bcs()
    print("\nALL KILL-GATE TESTS PASSED")
