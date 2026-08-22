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



def test_overview_queries():
    """总览页查询：温度历史/涨跌榜/拥挤度×强度/数据健康。"""
    th = q.load_temperature_history(DB)
    assert not th.empty and {"run_date", "score"} <= set(th.columns)
    mv = q.load_latest_movers(DB)
    assert not mv.empty and {"industry", "pct", "amount"} <= set(mv.columns)
    assert mv["pct"].max() > 0
    cs = q.load_crowding_vs_strength(DB)
    assert not cs.empty and {"obj", "rs", "crowding", "trend_stage"} <= set(cs.columns)
    h = q.load_data_health(DB)
    assert not h.empty and {"tbl", "max_date", "lag_days", "status"} <= set(h.columns)
    assert set(h["tbl"]) >= {"industry_bars", "index_bars", "daily_bars"}
    print("test_overview_queries OK")



def test_rotation_linkage_style_queries():
    """轮动轨迹/联动网络/风格时间线查询。"""
    rh = q.load_rotation_history(DB)
    assert not rh.empty and {"run_date", "industry", "rank"} <= set(rh.columns)
    edges = q.load_linkage_edges(DB, threshold=0.85, max_edges=150)
    assert not edges.empty and {"a", "b", "corr", "lead"} <= set(edges.columns)
    assert len(edges) <= 150
    assert (edges["corr"] >= 0.85).all()
    sh = q.load_style_history(DB)
    assert not sh.empty and {"run_date", "style", "n"} <= set(sh.columns)
    print("test_rotation_linkage_style_queries OK")


def test_position_limit():
    """评级→建议仓位（未评级时保守默认 0.5，不报错）。"""
    pl = q.load_position_limit(DB)
    assert set(pl) == {"macro", "market", "position_limit"}
    assert 0 <= pl["position_limit"] <= 1
    print("test_position_limit OK")

if __name__ == "__main__":
    test_queries()
    test_strength_industry_only()
    test_viewpoints_status_parameterized()
    test_overview_queries()
    test_rotation_linkage_style_queries()
    test_position_limit()
    print("\nALL DASHBOARD TESTS PASSED")