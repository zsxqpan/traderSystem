"""仪表盘查询测试（只读，用真实库）。用法: python tests/test_dashboard.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import queries as q

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "invest.db")


def test_queries():
    assert not q.load_strength(DB).empty
    assert {"obj", "rs", "trend_stage"} <= set(q.load_strength(DB).columns)
    assert not q.load_temperature(DB).empty
    cov = q.load_coverage(DB)
    assert len(cov) == 5 and {"tbl", "rows", "min_date", "max_date"} <= set(cov.columns)
    bt = q.load_backtests(DB)
    assert len(bt) >= 3  # trend_stage/style/style_temp 已入库
    vp = q.load_viewpoints(DB)
    assert {"id", "conclusion", "status"} <= set(vp.columns)
    acc = q.load_accuracy(DB)
    assert {"group", "verified", "invalidated", "accuracy"} <= set(acc.columns)
    print("test_queries OK")




def test_strength_industry_only():
    """回归：行业榜不再混入个股。"""
    import sqlite3
    from invest.db import connect as db_connect
    df = q.load_strength(DB)
    conn = db_connect(DB)
    try:
        stock_objs = {r[0] for r in conn.execute(
            "SELECT DISTINCT obj FROM quant_strength WHERE obj_type='stock'")}
    finally:
        conn.close()
    assert not (stock_objs & set(df["obj"].tolist())), stock_objs & set(df["obj"].tolist())
    print("test_strength_industry_only OK")


def test_viewpoints_status_parameterized():
    """回归：status/limit 参数化，单引号注入不生效也不报错。"""
    df = q.load_viewpoints(DB, status="active' OR '1'='1")
    assert df.empty
    jobs = q.load_jobs(DB, limit=5)
    assert len(jobs) <= 5
    print("test_viewpoints_status_parameterized OK")

if __name__ == "__main__":
    test_queries()
    test_strength_industry_only()
    test_viewpoints_status_parameterized()
    print("\nALL DASHBOARD TESTS PASSED")