"""组合风险簇单元测试。用法: python tests/test_clusters.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.discipline import pool
from invest.discipline.clusters import (
    check_cluster_budgets,
    cluster_type,
    exposure_report,
    industry_to_cluster,
    merge_cross_cycle,
    symbol_cluster,
)


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_clusters_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_industry_mapping():
    assert industry_to_cluster("半导体") == "AI科技"
    assert industry_to_cluster("银行") == "高股息"  # 首个命中：高股息优先于金融地产
    assert industry_to_cluster("房地产") == "地产链"
    assert industry_to_cluster("白酒") == "大消费"
    assert industry_to_cluster("汽车零部件") == "出口链"  # 出口链优先于汽车
    assert industry_to_cluster("不存在的行业") == "其他"
    assert industry_to_cluster("") == "其他"
    assert cluster_type("高股息") == "style"
    assert cluster_type("AI科技") == "event"
    assert cluster_type("其他") == "other"
    print("test_industry_mapping OK")


def test_symbol_cluster_and_merge():
    p = _tmp_db()
    conn = connect(p)
    pool.add_to_pool(conn, "600519", level="core", industry="白酒")
    pool.add_to_pool(conn, "002371", level="core", industry="半导体")
    pool.add_to_pool(conn, "000001", level="core", industry="银行")
    assert symbol_cluster(conn, "600519") == "大消费"
    assert symbol_cluster(conn, "002371") == "AI科技"
    assert symbol_cluster(conn, "000001") == "高股息"
    # 跨周期合并：同标的两张卡片（波段+配置）合并
    merged = merge_cross_cycle([
        {"symbol": "600519", "weight": 0.10, "cycle": "波段"},
        {"symbol": "600519", "weight": 0.05, "cycle": "配置"},
        {"symbol": "002371", "weight": 0.08, "cycle": "波段"},
    ])
    by_sym = {m["symbol"]: m for m in merged}
    assert by_sym["600519"]["weight"] == 0.15
    assert sorted(by_sym["600519"]["cycles"]) == ["波段", "配置"]
    assert by_sym["002371"]["weight"] == 0.08
    conn.close()
    print("test_symbol_cluster_and_merge OK")


def test_budget_violations():
    p = _tmp_db()
    conn = connect(p)
    pool.add_to_pool(conn, "600519", level="core", industry="白酒")       # 大消费(style)
    pool.add_to_pool(conn, "000858", level="core", industry="白酒")       # 大消费(style)
    pool.add_to_pool(conn, "601318", level="core", industry="保险")       # 金融地产(style)
    pool.add_to_pool(conn, "002371", level="core", industry="半导体")     # AI科技(event)
    pool.add_to_pool(conn, "300750", level="core", industry="电池")       # 新能源(event)
    # 大消费 25% + 金融地产 30% = 风格 55% < 60% OK
    positions = [
        {"symbol": "600519", "weight": 0.15, "cycle": "波段"},
        {"symbol": "000858", "weight": 0.10, "cycle": "配置"},
        {"symbol": "601318", "weight": 0.30, "cycle": "波段"},
        {"symbol": "002371", "weight": 0.05, "cycle": "波段"},
        {"symbol": "300750", "weight": 0.10, "cycle": "配置"},
    ]
    v = check_cluster_budgets(conn, positions)
    # 金融地产 30% <= 40%（单簇上限）但 style_total 55% < 60%
    assert not any("风格簇" in x for x in v)
    # 单簇超限：AI科技 50%
    positions2 = [
        {"symbol": "002371", "weight": 0.30, "cycle": "波段"},
        {"symbol": "300750", "weight": 0.20, "cycle": "配置"},
    ]
    v2 = check_cluster_budgets(conn, positions2)
    # 新能源 20% + AI科技 30% 分开算，AI科技单簇 30% <= 40%，新能源 20% <= 40%，event 仅军工/地产/出口/政策敏感
    assert not any("风险簇" in x for x in v2)
    # 事件博弈超限：地产链 25%
    pool.add_to_pool(conn, "000002", level="core", industry="房地产")
    positions3 = [{"symbol": "000002", "weight": 0.25, "cycle": "波段"}]
    v3 = check_cluster_budgets(conn, positions3)
    assert any("事件博弈" in x for x in v3)
    conn.close()
    print("test_budget_violations OK")


def test_exposure_report_shape():
    p = _tmp_db()
    conn = connect(p)
    pool.add_to_pool(conn, "600519", level="core", industry="白酒")
    report = exposure_report(conn, [{"symbol": "600519", "weight": 0.10}])
    assert "clusters" in report and "violations" in report
    assert report["clusters"].get("大消费") == 0.10
    assert report["style_total"] == 0.10
    conn.close()
    print("test_exposure_report_shape OK")


if __name__ == "__main__":
    test_industry_mapping()
    test_symbol_cluster_and_merge()
    test_budget_violations()
    test_exposure_report_shape()
    print("\nALL CLUSTERS TESTS PASSED")
