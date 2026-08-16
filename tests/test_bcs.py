"""BCS/VMS 双百分制评估单元测试。用法: python tests/test_bcs.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.review.bcs import bcs_score, full_assessment, veto_check, vms_score


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_bcs_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_bcs_score():
    # 全满分
    r = bcs_score(n_samples=600, oos_done=True, cost_model=True, param_robust=True, data_quality=True)
    assert r["score"] == 100.0 and r["grade"] == "A"
    # 全缺
    r2 = bcs_score()
    assert r2["score"] < 20 and r2["grade"] == "D"
    # 中间态
    r3 = bcs_score(n_samples=100, oos_done=True, cost_model=True)
    assert 60 <= r3["score"] <= 80
    print("test_bcs_score OK")


def test_vms_score():
    r = vms_score(versioned=True, factor_tested=True, discipline_tracked=True, review_loop=True, auto_degrade=True)
    assert r["score"] == 100.0 and r["grade"] == "A"
    r2 = vms_score()
    assert r2["score"] == 0.0
    print("test_vms_score OK")


def test_veto_check():
    p = _tmp_db()
    conn = connect(p)
    # 干净库：通过（realtime 默认 ok）
    r = veto_check(conn, realtime_ok=True, data_fresh=True)
    assert r["passed"] is True
    # 实时行情失效 -> 否决
    r2 = veto_check(conn, realtime_ok=False, data_fresh=True)
    assert r2["passed"] is False
    assert any("实时行情失效" in v for v in r2["violations"])
    # 数据陈旧 -> 否决
    r3 = veto_check(conn, realtime_ok=True, data_fresh=False)
    assert r3["passed"] is False
    # 无止损计划 -> 否决
    conn.execute(
        """INSERT INTO trade_plans(symbol, status, created_at)
           VALUES('X1', 'active', datetime('now','localtime'))"""
    )
    conn.commit()
    r4 = veto_check(conn, realtime_ok=True, data_fresh=True)
    assert r4["passed"] is False
    assert any("无止损" in v for v in r4["violations"])
    conn.close()
    print("test_veto_check OK")


def test_full_assessment():
    p = _tmp_db()
    conn = connect(p)
    r = full_assessment(conn, n_samples=600, oos_done=True)
    assert "bcs" in r and "vms" in r and "veto" in r
    assert r["overall"] in ("通过", "不通过")
    conn.close()
    print("test_full_assessment OK")


if __name__ == "__main__":
    test_bcs_score()
    test_vms_score()
    test_veto_check()
    test_full_assessment()
    print("\nALL BCS TESTS PASSED")
