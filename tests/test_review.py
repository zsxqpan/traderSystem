"""复盘引擎单元测试。用法: python tests/test_review.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.review.monthly import monthly_review
from invest.review.weekly import weekly_review
from invest.review.yearly import yearly_review


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_review_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_weekly_review():
    p = _tmp_db()
    conn = connect(p)
    # 正常计划 + 一笔正常记录
    conn.execute("""INSERT INTO trade_plans(symbol, stop_loss, status, created_at)
                    VALUES('X1', 9.0, 'active', datetime('now','localtime'))""")
    pid = conn.execute("SELECT id FROM trade_plans").fetchone()["id"]
    conn.execute("""INSERT INTO trade_records(plan_id, action, price, qty, actual_vs_plan, created_at)
                    VALUES(?, 'buy', 10.0, 100, 'in_range', datetime('now','localtime'))""", (pid,))
    # 计划外交易：已关闭计划仍有成交
    conn.execute("""INSERT INTO trade_plans(symbol, stop_loss, status, created_at)
                    VALUES('X2', 9.0, 'closed', datetime('now','localtime'))""")
    pid2 = conn.execute("SELECT id FROM trade_plans WHERE symbol='X2'").fetchone()["id"]
    conn.execute("""INSERT INTO trade_records(plan_id, action, price, qty, actual_vs_plan, created_at)
                    VALUES(?, 'buy', 10.0, 100, 'in_range', datetime('now','localtime'))""", (pid2,))
    r = weekly_review(conn)
    assert r["rogue_trades"] == 1
    assert r["trade_records"] == 2
    assert 0 <= r["score"] <= 100
    assert any("计划外" in v for v in r["violations"])
    conn.close()
    print("test_weekly_review OK")


def test_monthly_review():
    p = _tmp_db()
    conn = connect(p)
    from invest.viewpoints.store import create_viewpoint
    for i in range(2):
        v = create_viewpoint(conn, source="research", conclusion="看多", period_tag="short",
                             confidence=0.6, evidence=[{"x": 1}], invalid_condition="x")
        conn.execute("UPDATE viewpoints SET status='verified' WHERE id=?", (v,))
    v = create_viewpoint(conn, source="research", conclusion="看空", period_tag="short",
                         confidence=0.6, evidence=[{"x": 1}], invalid_condition="x")
    conn.execute("UPDATE viewpoints SET status='invalidated' WHERE id=?", (v,))
    conn.commit()
    r = monthly_review(conn)
    assert r["verified"] == 2 and r["invalidated"] == 1
    assert abs(r["overall_accuracy"] - round(2 / 3, 4)) < 1e-9
    conn.close()
    print("test_monthly_review OK")


def test_yearly_review():
    p = _tmp_db()
    conn = connect(p)
    conn.execute("""INSERT INTO backtest_runs(rule_type, params_json, metrics_json, dataset_range)
                    VALUES('trend_stage_excess', '{}', '[{\"signal\":\"启动\",\"mean\":0.01}]', 'a~b')""")
    conn.commit()
    r = yearly_review(conn)
    assert len(r["backtest_summary"]) == 1
    assert r["backtest_summary"][0]["rule_type"] == "trend_stage_excess"
    # 新结构：等级单调/凯利校准/权重区分度/错误分类
    assert "level_monotonicity" in r
    assert "kelly_calibration" in r
    assert "weight_discrimination" in r
    assert "error_classification" in r
    conn.close()
    print("test_yearly_review OK")


if __name__ == "__main__":
    test_weekly_review()
    test_monthly_review()
    test_yearly_review()
    print("\nALL REVIEW TESTS PASSED")