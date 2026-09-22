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
    """总览页查询：温度历史/拥挤度×强度/数据健康（2026-09-18 删板块涨跌热力图）。"""
    th = q.load_temperature_history(DB)
    assert not th.empty and {"run_date", "score"} <= set(th.columns)
    assert not hasattr(q, "load_latest_movers"), "当日板块涨跌热力图已删，查询函数不应保留"
    cs = q.load_crowding_vs_strength(DB)
    assert not cs.empty and {"obj", "rs", "crowding", "trend_stage"} <= set(cs.columns)
    h = q.load_data_health(DB)
    assert not h.empty and {"tbl", "max_date", "lag_days", "status"} <= set(h.columns)
    assert set(h["tbl"]) >= {"industry_bars", "index_bars", "daily_bars"}
    print("test_overview_queries OK")



def test_data_health_reference_and_macro_month_parse():
    """健康判定以「最近已收盘交易日」为参照（2026-09-15 修）：交易日内缺当日数据要显偏旧，
    月度口径（macro_series 的 2026年08月份 / 2026-08）要能解析出日期而不是 NaT。"""
    import pandas as pd

    # 参照日：交易日 15:30 后=当天；盘前/盘中=上一交易日；周末回退到周五
    assert q._last_closed_trading_day(pd.Timestamp("2026-09-15 16:30")).date().isoformat() == "2026-09-15"
    assert q._last_closed_trading_day(pd.Timestamp("2026-09-15 09:00")).date().isoformat() == "2026-09-14"
    assert q._last_closed_trading_day(pd.Timestamp("2026-09-19 12:00")).date().isoformat() == "2026-09-18"

    # 状态阈值：容差 0 → 0 正常 / 1-2 偏旧 / ≥3 过期；dragon_tiger 容 1 天
    assert q._health_status(0, 0) == "正常"
    assert q._health_status(1, 0) == "偏旧"
    assert q._health_status(3, 0) == "过期"
    assert q._health_status(1, 1) == "正常"
    assert q._health_status(float("nan"), 0) == "过期"

    # 月度解析：中文月份/紧凑月份/月度首日 都能落到该月月末
    for raw in ("2026年08月份", "2026-08", "202608", "2026-08-01"):
        parsed = q._parse_month(raw)
        assert parsed is not None, raw
        assert parsed.date().isoformat() == "2026-08-31", (raw, parsed)

    h = q.load_data_health(DB)
    assert "ref_date" in h.columns
    macro = h[h["tbl"] == "macro_series"]
    assert not macro.empty
    assert pd.notna(macro.iloc[0]["max_date"]), "macro_series 月份日期必须能解析（原先恒为 NaT/过期）"
    assert macro.iloc[0]["status"] in {"正常", "偏旧", "过期"}
    print("test_data_health_reference_and_macro_month_parse OK")


def test_rotation_linkage_style_queries():
    """轮动轨迹/风格时间线查询（2026-09-18 删行业联动网络图；短线轨「高相关行业对」表保留）。"""
    rh = q.load_rotation_history(DB)
    assert not rh.empty and {"run_date", "industry", "rank"} <= set(rh.columns)
    assert not hasattr(q, "load_linkage_edges"), "联动网络图已删，查询函数不应保留"
    lk = q.load_linkage(DB)
    assert not lk.empty
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
    test_data_health_reference_and_macro_month_parse()
    test_rotation_linkage_style_queries()
    test_position_limit()
    print("\nALL DASHBOARD TESTS PASSED")