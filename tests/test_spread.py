"""比价与因子 v1 单元测试。用法: python tests/test_spread.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from invest.db import connect, init_db
from invest.discipline.spread import (
    factor_score,
    industry_pe_spread,
    percentile_rank,
    price_spread,
    spread_analysis,
    suggest_reference,
    z_score_mad,
)
from invest.data.storage import upsert_df


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_spread_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_percentile_and_z():
    s = pd.Series(np.arange(1.0, 101.0))  # 1..100
    # 值 20 的分位 ~0.2
    assert abs(percentile_rank(s, 20.0) - 0.20) < 0.05
    # 值 90 的分位 ~0.9
    assert abs(percentile_rank(s, 90.0) - 0.90) < 0.05
    # 样本不足
    assert percentile_rank(pd.Series([1.0, 2.0]), 1.5) is None
    # Z 分：中位数 50.5，MAD 25，值 75.75 的 Z = 25.25/(1.4826*25) ≈ 0.68
    z = z_score_mad(s, 75.75)
    assert z is not None and 0.5 < z < 0.9
    print("test_percentile_and_z OK")


def test_spread_analysis():
    s = pd.Series(np.arange(10.0, 110.0))  # 10..109
    r = spread_analysis(s, 30.0)
    assert r["ok"] is True
    assert r["pct_rank"] is not None and r["pct_rank"] < 0.3  # 便宜
    assert r["cheap"] is True
    assert r["z_score"] < 0
    assert len(r["anchor_range"]) == 2
    # 样本不足
    r2 = spread_analysis(pd.Series([1.0, 2.0]), 1.5)
    assert r2["ok"] is False
    print("test_spread_analysis OK")


def test_industry_pe_spread():
    p = _tmp_db()
    conn = connect(p)
    # 造 3 年 PE 历史：从 40 缓降到 20
    import datetime as dt
    rows = []
    d = dt.date(2023, 1, 1)
    for i in range(180):
        rows.append({"date": (d + dt.timedelta(days=7 * i)).isoformat(),
                     "industry": "白酒", "pe": 40.0 - i * 0.1, "level": 1, "src": "akshare"})
    upsert_df(conn, "industry_valuation", pd.DataFrame(rows))
    r = industry_pe_spread(conn, "白酒")
    assert r["ok"] is True
    assert r["n_samples"] >= 50
    # 当前 PE ~22，历史高位 40 -> 分位低、Z 负
    assert r["pct_rank"] is not None and r["pct_rank"] < 0.3
    assert r["z_score"] < 0
    # 无数据行业
    r2 = industry_pe_spread(conn, "不存在行业")
    assert r2["ok"] is False
    conn.close()
    print("test_industry_pe_spread OK")


def test_price_spread():
    p = _tmp_db()
    conn = connect(p)
    import datetime as dt
    rows = []
    d = dt.date(2024, 1, 1)
    for i in range(200):
        rows.append({"symbol": "600519", "date": (d + dt.timedelta(days=i)).isoformat(),
                     "close": 100.0 + i * 0.5, "src": "akshare"})
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))
    r = price_spread(conn, "600519")
    assert r["ok"] is True
    assert r["n_samples"] >= 100
    conn.close()
    print("test_price_spread OK")


def test_factor_score():
    # 错价 4 分 + 修复 3 分 + 背景 5 分（不占权重）
    r = factor_score([
        {"name": "低估", "score": 4, "role": "错价"},
        {"name": "景气修复", "score": 3, "role": "修复"},
        {"name": "宏观", "score": 5, "role": "背景"},
    ])
    assert r["ok"] is True
    assert abs(r["total"] - 3.5) < 1e-9  # (4+3)/2，背景不计
    assert r["grade"] == "A"
    # 风险过滤权重 0.5
    r2 = factor_score([{"name": "x", "score": 2, "role": "风险过滤"}])
    assert r2["total"] == 2.0
    # 空因子
    r3 = factor_score([])
    assert r3["ok"] is False
    print("test_factor_score OK")


def test_suggest_reference():
    p = _tmp_db()
    conn = connect(p)
    assert suggest_reference("白酒", conn) == "全A（无行业估值数据时用中性化基准）"
    conn.execute(
        "INSERT INTO industry_valuation(date, industry, pe, level, src) VALUES('2026-08-14','白酒',20,1,'akshare')"
    )
    conn.commit()
    assert "行业等权" in suggest_reference("白酒", conn)
    conn.close()
    print("test_suggest_reference OK")


if __name__ == "__main__":
    test_percentile_and_z()
    test_spread_analysis()
    test_industry_pe_spread()
    test_price_spread()
    test_factor_score()
    test_suggest_reference()
    print("\nALL SPREAD TESTS PASSED")
