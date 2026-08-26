"""定量层单元测试（合成数据，无网络）。用法: python tests/test_quant.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from invest.quant.rotation import compute_rotation
from invest.quant.strength import calc_rs, calc_trend_stage, compute_strength
from invest.quant.temperature import compute_temperature


def _make_closes(n=120, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n)
    # 基准：温和上行
    bench = pd.Series(100 * np.cumprod(1 + rng.normal(0.0002, 0.008, n)), index=dates)
    # A：强趋势上行；B：持续下跌；C：横盘
    a = pd.Series(100 * np.cumprod(1 + rng.normal(0.003, 0.01, n)), index=dates)
    b = pd.Series(100 * np.cumprod(1 + rng.normal(-0.004, 0.01, n)), index=dates)
    c = pd.Series(100 * np.cumprod(1 + rng.normal(0.0001, 0.005, n)), index=dates)
    closes = pd.DataFrame({"A": a, "B": b, "C": c}, index=dates)
    amounts = pd.DataFrame({
        "A": np.linspace(100, 120, n), "B": np.linspace(100, 80, n), "C": np.linspace(100, 100, n),
    }, index=dates)
    returns = closes.pct_change().replace([np.inf, -np.inf], np.nan)
    return closes, amounts, returns, bench


def test_strength_ranking():
    closes, _, _returns, bench = _make_closes()
    df = compute_strength(closes, bench)
    assert df["obj"].tolist() == ["A", "B", "C"]
    rs = dict(zip(df["obj"], df["rs"]))
    assert rs["A"] > rs["C"] > rs["B"], rs
    assert df["obj_type"].iloc[0] == "industry"
    assert df["period"].iloc[0] == "short"
    assert {"rs5", "rs10", "rs20"} <= set(df.columns)
    assert df["rs5"].notna().all() and df["rs10"].notna().all() and df["rs20"].notna().all()
    print("test_strength_ranking OK")


def test_calc_rs_direction():
    closes, _, _, bench = _make_closes()
    r = calc_rs(closes["A"], bench, [5, 10, 20], [0.2, 0.3, 0.5])
    assert r > 0
    r2 = calc_rs(closes["B"], bench, [5, 10, 20], [0.2, 0.3, 0.5])
    assert r2 < 0
    print("test_calc_rs_direction OK")


def test_calc_rs_duplicate_index():
    """2026-08-25 回归：index 重复（index_bars snapshot/akshare 双行）不再崩 concat——
    防御去重保留最后一条，结果与去重前一致。"""
    closes, _, _, bench = _make_closes()
    r_clean = calc_rs(closes["A"], bench, [5, 10, 20], [0.2, 0.3, 0.5])
    # 构造重复 index（bench 最后一条日期出现两次）
    bench_dup = pd.concat([bench, bench.iloc[[-1]]])  # 末尾重复
    assert bench_dup.index.duplicated().any()
    r_dup = calc_rs(closes["A"], bench_dup, [5, 10, 20], [0.2, 0.3, 0.5])
    assert abs(r_dup - r_clean) < 1e-9  # 去重后结果一致
    print("test_calc_rs_duplicate_index OK")


def test_trend_stage():
    dates = pd.bdate_range("2024-01-02", periods=120)
    up = pd.Series(100 * np.full(120, 1.003).cumprod(), index=dates)   # 持续上行
    down = pd.Series(100 * np.full(120, 0.996).cumprod(), index=dates)  # 持续下行
    assert calc_trend_stage(up) in ("加速", "减速")
    assert calc_trend_stage(down) == "破位"
    print("test_trend_stage OK")


def test_rotation():
    _closes, amounts, returns, _ = _make_closes()
    df = compute_rotation(returns, amounts)
    assert len(df) == 3
    assert set(df.columns) == {"run_date", "industry", "rank", "lead_lag", "turnover_share"}
    assert df["rank"].min() == 1 and df["rank"].max() == 3
    assert set(df["lead_lag"]) <= {"领涨", "滞后", "同步"}
    assert abs(df["turnover_share"].sum() - 1.0) < 1e-6
    print("test_rotation OK")


def test_rotation_nan_rank():
    """回归：最新一期某行业缺数时不应崩溃，缺数行业排名置底。"""
    _closes, amounts, returns, _ = _make_closes()
    returns.loc[returns.index[-1], "B"] = np.nan
    df = compute_rotation(returns, amounts)
    assert len(df) == 3
    rank = dict(zip(df["industry"], df["rank"]))
    assert rank["B"] == 3
    assert df["rank"].min() == 1 and df["rank"].max() == 3
    print("test_rotation_nan_rank OK")


def test_temperature():
    _closes, amounts, returns, _ = _make_closes()
    df = compute_temperature(returns, amounts)
    assert len(df) == 1
    score = df.iloc[0]["score"]
    assert 0 <= score <= 100
    assert 0 <= df.iloc[0]["profit_effect"] <= 1
    print("test_temperature OK")




def test_capital_style():
    from invest.quant.capital import classify_style
    dates = pd.bdate_range("2024-01-02", periods=120)
    # 产业趋势：低波动稳步上行
    steady = pd.Series(100 * np.full(120, 1.005).cumprod(), index=dates)
    # 主题炒作：高波动剧烈脉冲
    rng = np.random.default_rng(3)
    rets = rng.normal(0.0, 0.025, 120)
    rets[115:120] = 0.06   # 尾部脉冲
    choppy = pd.Series(100 * np.cumprod(1 + rets), index=dates)
    r_steady = steady.pct_change()
    r_choppy = choppy.pct_change()
    assert classify_style(r_steady, steady) == "产业趋势"
    assert classify_style(r_choppy, choppy) == "主题炒作"
    print("test_capital_style OK")


def test_linkage():
    from invest.quant.linkage import compute_linkage
    dates = pd.bdate_range("2024-01-02", periods=120)
    rng = np.random.default_rng(5)
    noise = rng.normal(0.0001, 0.005, 120)
    common = rng.normal(0.001, 0.01, 120)
    a = pd.Series(100 * np.cumprod(1 + common), index=dates)
    b = pd.Series(100 * np.cumprod(1 + common + noise * 0.1), index=dates)  # 与 A 高度同步
    c = pd.Series(100 * np.cumprod(1 + rng.normal(0.0001, 0.01, 120)), index=dates)  # 独立
    rets = pd.DataFrame({"A": a, "B": b, "C": c}).pct_change()
    df = compute_linkage(rets)
    pairs = {(r["a"], r["b"]) for _, r in df.iterrows()}
    assert ("A", "B") in pairs, pairs
    assert ("A", "C") not in pairs
    print("test_linkage OK")



def test_weekly_strength():
    from invest.quant.weekly import compute_weekly
    closes, _, _, bench = _make_closes(n=400, seed=11)
    df = compute_weekly(closes, bench)
    assert len(df) >= 2
    assert (df["period"] == "mid").all()
    rs = dict(zip(df["obj"], df["rs"]))
    assert rs["A"] > rs["B"], rs
    print("test_weekly_strength OK")


def test_crowding():
    from invest.quant.crowding import compute_crowding
    n = 300
    dates = pd.bdate_range("2024-01-02", periods=n)
    amt = pd.DataFrame({
        "HOT": np.concatenate([np.full(n // 2, 50.0), np.linspace(50, 500, n // 2)]),
        "COLD": np.concatenate([np.full(n // 2, 50.0), np.linspace(50, 10, n // 2)]),
        "FLAT": np.full(n, 50.0),
    }, index=dates)
    df = compute_crowding(amt)
    c = dict(zip(df["obj"], df["crowding"]))
    assert c["HOT"] > 0.9, c
    assert c["COLD"] <= 0.5, c
    print("test_crowding OK")


def test_macro_liquidity():
    from invest.quant.macro_liquidity import compute_macro_liquidity
    m = pd.DataFrame({
        "date": ["2024-05", "2024-06"],
        "indicator": ["货币(M1)-同比增长", "货币(M1)-同比增长"],
        "value": [1.0, 1.5],
    })
    m2 = pd.DataFrame({
        "date": ["2024-06"],
        "indicator": ["货币和准货币(M2)-同比增长"],
        "value": [7.0],
    })
    m3 = pd.DataFrame({
        "date": ["2024-06"],
        "indicator": ["制造业-指数"],
        "value": [49.5],
    })
    m4 = pd.DataFrame({
        "date": ["2026年03月份"],
        "indicator": ["社会融资规模增量"],
        "value": [52240.0],
    })
    out = compute_macro_liquidity(pd.concat([m, m2, m3, m4], ignore_index=True))
    d = dict(zip(out["indicator"], out["value"]))
    assert abs(d["M1-M2剪刀差"] - (1.5 - 7.0)) < 1e-6, d
    assert d["PMI制造业指数"] == 49.5
    assert d["社融增量"] == 52240.0  # 真实社融增量已接入（2026-08-15）
    print("test_macro_liquidity OK")



def test_temperature_with_emotion():
    from invest.quant.temperature import compute_temperature, temperature_series
    _closes, amounts, returns, _ = _make_closes(n=120, seed=21)
    emotion = pd.DataFrame({
        "date": returns.index,
        "limit_up_count": np.concatenate([np.full(60, 30.0), np.linspace(30, 120, 60)]),
        "max_lianban": np.concatenate([np.full(60, 2.0), np.linspace(2, 9, 60)]),
        "zhaban_rate": np.concatenate([np.full(60, 0.5), np.linspace(0.5, 0.2, 60)]),
    })
    s_no = temperature_series(returns, amounts, emotion=None)
    s_em = temperature_series(returns, amounts, emotion=emotion)
    assert s_em.iloc[-1] > s_no.iloc[-1], (s_em.iloc[-1], s_no.iloc[-1])
    row = compute_temperature(returns, amounts, emotion=emotion)
    assert row.iloc[0]["limit_up_count"] is not None
    assert row.iloc[0]["max_lianban"] is not None
    print("test_temperature_with_emotion OK")


def test_backfill_emotion_tasks():
    from invest.data.backfill import build_emotion_tasks
    tasks = build_emotion_tasks(days=10)
    assert len(tasks) == 10
    assert all(t["kind"] == "market_emotion" for t in tasks)
    assert tasks[0]["params"]["date"] != tasks[1]["params"]["date"]
    print("test_backfill_emotion_tasks OK")



def test_seat_classification_and_stock_capital():
    from invest.quant.capital import (
        aggregate_fund_types,
        classify_seat,
        compute_stock_capital,
    )
    assert classify_seat("机构专用") == "机构"
    assert classify_seat("深股通专用") == "北向"
    assert classify_seat("某某量化营业部") == "量化"
    assert classify_seat("中信证券上海分公司") == "游资"
    # 榜单占位行不得误分类（2026-08-15 修复：'list'/空 曾落入默认"游资"分支）
    assert classify_seat("list") is None
    assert classify_seat(None) is None

    seats = pd.DataFrame({
        "symbol": ["000001", "000001", "000001", "600000", "600000", "000002", "000002"],
        "seat_type": ["机构专用", "机构专用", "游资X", "深股通专用", "深股通专用", "list", None],
        "net": [1000.0, 900.0, -500.0, 300.0, 200.0, 9999.0, 9999.0],
    })
    ft = aggregate_fund_types(seats)
    assert ft["000001"]["fund_type"] == "机构"
    assert ft["000001"]["confidence"] > 0.5
    assert ft["600000"]["fund_type"] == "北向"
    # 只有占位行的标的不得产出 fund_type（防假数据）
    assert "000002" not in ft or ft["000002"]["fund_type"] is None

    dates = pd.bdate_range("2024-01-02", periods=120)
    closes = pd.DataFrame({
        "000001": pd.Series(100 * np.full(120, 1.003).cumprod(), index=dates),
        "600000": pd.Series(100 * np.full(120, 1.002).cumprod(), index=dates),
    })
    rets = closes.pct_change()
    cap = compute_stock_capital(closes, rets, ft)
    assert (cap["obj_type"] == "stock").all()
    assert cap.set_index("obj").loc["000001", "fund_type"] == "机构"
    print("test_seat_classification_and_stock_capital OK")



def test_pe_percentile():
    from invest.quant.valuation import compute_pe_percentile, merge_valuation
    hist = pd.DataFrame({
        "date": ["2024-01"] * 3 + ["2026-08"] * 3,
        "industry": ["半导体", "半导体", "银行", "半导体", "半导体", "银行"],
        "pe": [30.0, 35.0, 5.0, 50.0, 40.0, 6.0],
    })
    pct = compute_pe_percentile(hist)
    d = dict(zip(pct["industry"], pct["pe_pct"]))
    assert d["半导体"] == 1.0      # 50 是历史最高
    assert d["银行"] == 1.0
    existing = pd.DataFrame({"obj": ["半导体", "银行"], "crowding": [0.5, 0.6]})
    merged = merge_valuation(existing, pct)
    assert merged.loc[0, "pe_pct"] == 1.0
    assert merged.loc[0, "crowding"] == 0.5
    print("test_pe_percentile OK")



def test_params_from_yaml():
    """回归：config.yaml indicators 段应覆盖代码默认参数。"""
    import invest.quant.indicators as ind
    ind._yaml_params.cache_clear()
    orig = ind.load_yaml_config

    def _fake(*args, **kwargs):
        return {"indicators": {"rotation": {"rank_change_threshold": 9}}}

    ind.load_yaml_config = _fake
    try:
        assert ind.get_params("rotation")["rank_change_threshold"] == 9
        # 未配置段仍走代码默认值
        assert ind.get_params("crowding")["window"] == 250
    finally:
        ind.load_yaml_config = orig
        ind._yaml_params.cache_clear()
    print("test_params_from_yaml OK")

if __name__ == "__main__":
    test_strength_ranking()
    test_calc_rs_direction()
    test_trend_stage()
    test_rotation()
    test_rotation_nan_rank()
    test_temperature()
    test_capital_style()
    test_linkage()
    test_weekly_strength()
    test_crowding()
    test_macro_liquidity()
    test_temperature_with_emotion()
    test_backfill_emotion_tasks()
    test_seat_classification_and_stock_capital()
    test_pe_percentile()
    test_params_from_yaml()
    print("\nALL QUANT TESTS PASSED")