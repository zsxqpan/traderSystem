"""TODO [A] 类新增功能单元测试（2026-08-15）：
[A]1 pb 分位 / [A]2 行业映射 / [A]3 L3 主题 / [A]4 结构断点 /
[A]5 发现器 / [A]6 周期漂移 / [A]7 复盘 v1 / [A]8 P2 简报 /
[A]9 快照重建 / [A]10 历史快照 / [A]11+12 因子价差自动化。
用法: python tests/test_todo_a.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from invest.db import connect, init_db
from invest.data.storage import upsert_df


def _tmp_db(name="invest_todo_a"):
    p = os.path.join(tempfile.gettempdir(), f"{name}_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed_daily(conn, symbol, days=80, adv=1e8, start="2026-01-01"):
    rows = []
    d = dt.date.fromisoformat(start)
    for i in range(days):
        rows.append({"symbol": symbol, "date": (d + dt.timedelta(days=i)).isoformat(),
                     "close": 10.0 + i * 0.01, "amount": adv, "src": "akshare"})
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))


# ---------- [A]2 个股→行业映射 ----------

def test_industry_map_load_and_query():
    from invest.data.industry_map import industry_of, load_industry_stocks, save_industry_stocks
    p = _tmp_db("industry_map")
    conn = connect(p)
    m = load_industry_stocks()
    assert "600519" in m and m["600519"] == "白酒"
    # 手工映射优先
    assert industry_of(conn, "600519", mapping=m) == "白酒"
    # 候选池兜底
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('000002','core','房地产',date('now','localtime'))")
    conn.commit()
    assert industry_of(conn, "000002") == "房地产"
    # 未命中
    assert industry_of(conn, "999999") == ""
    # save 持久化
    p2 = os.path.join(tempfile.gettempdir(), "industry_map_tmp.json")
    save_industry_stocks({"600000": "银行"}, path=p2)
    assert load_industry_stocks(path=p2)["600000"] == "银行"
    os.remove(p2)
    conn.close()
    print("test_industry_map_load_and_query OK")


# ---------- [A]3 L3 主题清单 ----------

def test_themes():
    from invest.data.themes import find_themes, load_themes, theme_by_id, themes_of_stock
    themes = load_themes()
    assert len(themes) >= 10  # 首批 ≥10
    assert len({t["id"] for t in themes}) == len(themes)
    assert theme_by_id("ai_calc", themes)["name"] == "AI算力"
    hits = find_themes(industry="半导体", themes=themes)
    assert any(t["id"] == "semi_equip" for t in hits)
    hits2 = find_themes(text="光模块", themes=themes)
    assert any(t["id"] == "optical_module" for t in hits2)
    # 标的 → 主题
    p = _tmp_db("themes")
    conn = connect(p)
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('002371','track','半导体',date('now','localtime'))")
    conn.commit()
    ts = themes_of_stock(conn, "002371", themes=themes)
    assert any(t["id"] == "semi_equip" for t in ts)
    conn.close()
    print("test_themes OK")


# ---------- [A]4 结构断点检查 ----------

def test_structural_break_truncation():
    from invest.discipline.spread import detect_level_breaks, truncate_at_break
    # 构造：前 100 个点水平 50，后 100 个点水平 20（明显步骤变化）
    dates = pd.Series(pd.bdate_range("2020-01-01", periods=200).strftime("%Y-%m-%d").tolist())
    values = pd.Series([50.0] * 100 + [20.0] * 100)
    breaks = detect_level_breaks(values, dates, k=3.0, window=30, min_samples=40)
    assert breaks, "应检测到结构断点"
    kept, kept_dates, info = truncate_at_break(
        values, dates, entity="白酒", k=3.0, window=30, min_samples=40
    )
    assert info["truncated"] is True
    # 截断后应只剩新口径（水平 20）部分
    assert kept.iloc[0] < 30
    # 已知断点优先
    values2 = pd.Series([50.0] * 100 + [20.0] * 100)
    dates2 = pd.Series(pd.bdate_range("2020-01-01", periods=200).strftime("%Y-%m-%d").tolist())
    _, _, info2 = truncate_at_break(
        values2, dates2, entity="白酒",
        known_breaks={"白酒": ["2020-06-01"]}, min_samples=40,
    )
    assert info2["truncated"] is True and info2["source"] == "known"
    assert info2["cutoff"] == "2020-06-01"
    # 无断点序列不截断
    flat = pd.Series(np.linspace(10, 12, 100))
    fdates = pd.Series([f"2024-01-{i % 28 + 1:02d}" for i in range(100)])
    _, _, info3 = truncate_at_break(flat, fdates, entity="X", min_samples=40)
    assert info3["truncated"] is False
    print("test_structural_break_truncation OK")


def test_industry_pe_spread_with_breaks():
    from invest.discipline.spread import industry_pe_spread
    p = _tmp_db("pe_breaks")
    conn = connect(p)
    import datetime as dt2
    rows = []
    d = dt2.date(2022, 1, 1)
    for i in range(150):
        pe = 45.0 if i < 100 else 22.0  # 100 天前口径 45 → 之后 22
        rows.append({"date": (d + dt.timedelta(days=7 * i)).isoformat(),
                     "industry": "白酒", "pe": pe, "level": 1, "src": "akshare"})
    upsert_df(conn, "industry_valuation", pd.DataFrame(rows))
    r = industry_pe_spread(conn, "白酒")
    assert r["ok"] is True
    assert r["break"]["truncated"] is True  # 结构断点已截断旧口径
    assert r["n_samples"] < 60  # 只保留新口径部分
    conn.close()
    print("test_industry_pe_spread_with_breaks OK")


# ---------- [A]5 榜单降级为「发现器」 ----------

def test_mispricing_necessary():
    from invest.discipline.spread import discover_eligible, mispricing_necessary
    # 分位低 → 通过
    ok, reason = mispricing_necessary({"ok": True, "pct_rank": 0.10, "z_score": -1.5})
    assert ok is True
    # 分位高 → 拒绝
    ok2, _ = mispricing_necessary({"ok": True, "pct_rank": 0.60, "z_score": 1.0})
    assert ok2 is False
    # 不可用 → 拒绝
    ok3, _ = mispricing_necessary({"ok": False, "note": "无数据"})
    assert ok3 is False
    # discover_eligible 走数据库
    p = _tmp_db("discover")
    conn = connect(p)
    r = discover_eligible(conn, "600519", industry="白酒")
    assert "eligible" in r and "reason" in r
    conn.close()
    print("test_mispricing_necessary OK")


def test_check_and_add_require_mispricing():
    from invest.discipline.pool_rules import check_and_add
    from invest.data.pit import list_decisions
    p = _tmp_db("mispricing_pool")
    conn = connect(p)
    _seed_daily(conn, "600519")
    # 无行业 PE 数据 → 价格分位可能高 → 需要便宜才入池；默认数据分位高 → 拒绝并留痕
    try:
        check_and_add(conn, "600519", level="track", industry="白酒", require_mispricing=True)
        added = True
    except ValueError:
        added = False
    # 若价格处于高位（10→18），分位接近 1 → 不满足错价必要条件
    d = list_decisions(conn, symbol="600519")
    if not added:
        assert any(x["decision"] == "reject" and "错价" in x["reason"] for x in d)
    else:
        # 允许宽松数据下通过（价格序列缓慢上行时 pct 接近 1，正常应拒绝）
        assert False, "价格单调上行不应通过错价必要条件"
    conn.close()
    print("test_check_and_add_require_mispricing OK")


# ---------- [A]6 周期漂移检测 ----------

def test_cycle_drift():
    from invest.discipline.records import detect_cycle_drift, drift_report
    from invest.discipline import plans
    p = _tmp_db("cycle_drift")
    conn = connect(p)
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('600519','core','白酒',date('now','localtime'))")
    # 卡片 cycle=short（上限 25 天）
    conn.execute("""INSERT INTO cards(symbol, level, cycle, thesis, status, created_at)
                    VALUES('600519','A','short','这是一个足够长的投资逻辑说明文本内容','locked',
                           datetime('now','localtime','-40 days'))""")
    plan = plans.create_plan(conn, "600519", stop_loss=9.0, buy_range="10,11")
    # 计划 40 天前建立（模拟超期持有）
    conn.execute(
        "UPDATE trade_plans SET created_at = datetime('now','localtime','-40 days') WHERE id=?",
        (plan["plan_id"],),
    )
    conn.commit()
    drifts = detect_cycle_drift(conn)
    assert len(drifts) == 1  # 40 天 > short 上限 25 天
    assert drifts[0]["plan_id"] == plan["plan_id"]
    assert drifts[0]["drift_days"] == 15
    rep = drift_report(conn)
    assert rep["n_drift"] == 1 and len(rep["violations"]) == 1
    conn.close()
    print("test_cycle_drift OK")


# ---------- [A]7 复盘 v1 ----------

def test_weekly_card_review_and_monthly_env():
    from invest.review.weekly import position_card_review, weekly_review
    from invest.review.monthly import environment_quality, monthly_review
    p = _tmp_db("review_a7")
    conn = connect(p)
    _seed_daily(conn, "600519")
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('600519','core','白酒',date('now','localtime'))")
    conn.execute("""INSERT INTO cards(symbol, level, cycle, thesis, status, stop_loss, target, created_at)
                    VALUES('600519','A','short','这是一个足够长的投资逻辑说明文本内容','locked', 18.5, 25.0,
                           datetime('now','localtime'))""")
    # 最新收盘 ~17（10+80*0.01=10.8 起算最后一天 ~10.79+...），实际 10+0.79=10.79 < 18.5*1.03 → near_stop
    review = position_card_review(conn)
    assert len(review) == 1
    assert review[0]["symbol"] == "600519"
    # 周度复盘含周期漂移与卡片复评字段
    w = weekly_review(conn)
    assert "cards_review" in w and "cycle_drift" in w
    # 月度环境质量
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-07-01','macro','宽松')")
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-01','macro','收紧')")
    conn.commit()
    env = environment_quality(conn)
    assert "rating_changes" in env and "data_quality" in env
    m = monthly_review(conn)
    assert "environment_quality" in m
    conn.close()
    print("test_weekly_card_review_and_monthly_env OK")


# ---------- [A]8 P2 简报 ----------

def test_p2_brief():
    from invest.pipeline import notify_p2_brief
    p = _tmp_db("p2brief")
    with mock.patch("invest.notifier.Notifier") as m:
        m.return_value.send_text.return_value = True
        assert notify_p2_brief(p, "test") is True
        text = m.return_value.send_text.call_args.args[0]
        assert "P2 例行简报" in text and "宏观流动性" in text
    print("test_p2_brief OK")


# ---------- [A]9 快照重建 ----------

def test_snapshot_rebuild():
    import json
    from invest.scan import rebuild_quant, rebuild_ratings, rebuild_snapshot, rebuild_pool
    snap_dir = os.path.join(tempfile.gettempdir(), "snap_rebuild_test")
    os.makedirs(snap_dir, exist_ok=True)
    for f in os.listdir(snap_dir):
        os.remove(os.path.join(snap_dir, f))
    with open(os.path.join(snap_dir, "2026-08-10.json"), "w", encoding="utf-8") as f:
        json.dump({"date": "2026-08-10", "pool": {"600519": {"level": "core"}},
                   "ratings": {"macro": "宽松"}, "quant": {"quant_strength": {"rows": 5}}}, f, ensure_ascii=False)
    with open(os.path.join(snap_dir, "2026-08-14.json"), "w", encoding="utf-8") as f:
        json.dump({"date": "2026-08-14", "pool": {"600519": {"level": "track"}},
                   "ratings": {"macro": "收紧"}, "quant": {"quant_strength": {"rows": 8}}}, f, ensure_ascii=False)
    # 08-12 应取 08-10（不晚于该日最近）
    snap = rebuild_snapshot("2026-08-12", snapshot_dir=snap_dir)
    assert snap["rebuilt"] is True and snap["date"] == "2026-08-10"
    # 08-14 取当日
    snap2 = rebuild_snapshot("2026-08-14", snapshot_dir=snap_dir)
    assert snap2["date"] == "2026-08-14" and snap2["pool"]["600519"]["level"] == "track"
    # 早于全部快照 → 空
    snap3 = rebuild_snapshot("2026-08-01", snapshot_dir=snap_dir)
    assert snap3["rebuilt"] is False
    # 单表重建
    rp = rebuild_pool("x", "2026-08-12", snapshot_dir=snap_dir)
    assert rp["pool"]["600519"]["level"] == "core"
    rr = rebuild_ratings("x", "2026-08-14", snapshot_dir=snap_dir)
    assert rr["ratings"]["macro"] == "收紧"
    rq = rebuild_quant("x", "2026-08-12", snapshot_dir=snap_dir)
    assert rq["quant"]["quant_strength"]["rows"] == 5
    print("test_snapshot_rebuild OK")


# ---------- [A]10 历史行业归属/ST 快照 ----------

def test_universe_snapshot():
    from invest.data.universe import (
        industry_at, record_universe_snapshot, st_at, universe_at,
    )
    from invest.data.industry_map import load_industry_stocks
    p = _tmp_db("universe")
    conn = connect(p)
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('600519','core','白酒',date('now','localtime'))")
    conn.commit()
    n = record_universe_snapshot(conn, date="2026-08-15", mapping=load_industry_stocks())
    assert n > 0
    rows = universe_at(conn, "2026-08-15")
    by_sym = {r["symbol"]: r for r in rows}
    assert "600519" in by_sym and by_sym["600519"]["industry"] == "白酒"
    assert industry_at(conn, "600519", "2026-08-15") == "白酒"
    assert st_at(conn, "600519", "2026-08-15") is False
    # ST 判定
    assert st_at(conn, "ST1001", "2026-08-15") is False  # 未记录 → False
    # 幂等：同日重复写
    n2 = record_universe_snapshot(conn, date="2026-08-15", mapping=load_industry_stocks())
    assert n2 == n
    conn.close()
    print("test_universe_snapshot OK")


# ---------- [A]11+12 因子与价差自动化 ----------

def test_cycle_mirrors_and_automation():
    from invest.discipline.auto import CYCLE_MIRRORS, auto_factor_score, run_pool_automation
    assert set(CYCLE_MIRRORS.keys()) == {"波段", "配置", "事件博弈", "趋势"}  # 四套周期镜像
    p = _tmp_db("auto")
    conn = connect(p)
    _seed_daily(conn, "600519", days=200, start="2024-01-01")
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('600519','core','白酒',date('now','localtime'))")
    # 价格序列 10 → 12（i*0.01），最新分位高 → 不满足错价必要条件（but 主价差可用）
    rep = auto_factor_score(conn, "600519", cycle="波段")
    assert rep["ok"] is True
    assert rep["factor_result"]["total"] > 0
    assert "主价差低估" in [f["name"] for f in rep["factors"]]
    # 全池自动化：四套镜像全启用
    out = run_pool_automation(conn)
    assert set(out["cycles"]) == {"波段", "配置", "事件博弈", "趋势"}
    assert out["summary"]["波段"]["n_pool"] == 1
    # 低分位标的应 eligible（构造下行序列）
    _seed_daily(conn, "000001", days=200, start="2024-01-01")
    conn.execute("DELETE FROM daily_bars WHERE symbol='000001'")
    rows = []
    d = dt.date(2024, 1, 1)
    for i in range(200):
        rows.append({"symbol": "000001", "date": (d + dt.timedelta(days=i)).isoformat(),
                     "close": 20.0 - i * 0.05, "amount": 1e8, "src": "akshare"})
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('000001','track','银行',date('now','localtime'))")
    conn.commit()
    rep2 = auto_factor_score(conn, "000001", cycle="配置")
    assert rep2["eligible"] is True  # 下行序列分位低
    conn.close()
    print("test_cycle_mirrors_and_automation OK")


# ---------- [A]1 行业 PB 分位 ----------

def test_pb_percentile():
    from invest.quant.valuation import compute_pb_percentile, compute_pe_percentile, merge_valuation
    hist = pd.DataFrame([
        {"date": "2026-01-31", "industry": "白酒", "pb": 3.0, "pe": 30.0},
        {"date": "2026-02-28", "industry": "白酒", "pb": 2.5, "pe": 25.0},
        {"date": "2026-03-31", "industry": "白酒", "pb": 2.0, "pe": 20.0},
        {"date": "2026-04-30", "industry": "白酒", "pb": 1.5, "pe": 15.0},
    ])
    pb = compute_pb_percentile(hist)
    assert pb.iloc[0]["pb_pct"] < 0.5  # 最新 1.5 是历史最低 → 分位低
    pe = compute_pe_percentile(hist)
    assert pe.iloc[0]["pe_pct"] < 0.5
    # merge 保留 crowding
    existing = pd.DataFrame([{"obj": "白酒", "crowding": 0.5}])
    out = merge_valuation(existing, pb, col_pct="pb_pct")
    assert out["pb_pct"].iloc[0] == pb.iloc[0]["pb_pct"]
    out2 = merge_valuation(existing, pe, col_pct="pe_pct")
    assert "pe_pct" in out2.columns
    print("test_pb_percentile OK")


if __name__ == "__main__":
    test_industry_map_load_and_query()
    test_themes()
    test_structural_break_truncation()
    test_industry_pe_spread_with_breaks()
    test_mispricing_necessary()
    test_check_and_add_require_mispricing()
    test_cycle_drift()
    test_weekly_card_review_and_monthly_env()
    test_p2_brief()
    test_snapshot_rebuild()
    test_universe_snapshot()
    test_cycle_mirrors_and_automation()
    test_pb_percentile()
    print("\nALL TODO-A TESTS PASSED")
