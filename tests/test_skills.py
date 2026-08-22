"""Skill 引擎测试（2026-08-22）：注册表完整性 + 逐字节一致 + 错误路径。

全部 mock/临时库，不连真实网络。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.data.storage import upsert_df
from invest.db import connect, init_db
from invest.skills import registry
from invest.skills.runner import run as run_skill


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_skills_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed(conn):
    """与 test_report._seed 相同的造数（保证逐字节一致测试可比对）。"""
    upsert_df(conn, "quant_temperature", pd.DataFrame([
        {"run_date": "2026-08-14", "score": 55.0, "profit_effect": 0.55},
    ]))
    upsert_df(conn, "industry_bars", pd.DataFrame([
        {"date": "2026-08-13", "industry": "A", "close": 9.0, "src": "akshare"},
        {"date": "2026-08-14", "industry": "A", "close": 10.0, "src": "akshare"},
        {"date": "2026-08-15", "industry": "A", "close": 11.0, "src": "akshare"},
        {"date": "2026-08-13", "industry": "B", "close": 19.0, "src": "akshare"},
        {"date": "2026-08-14", "industry": "B", "close": 20.0, "src": "akshare"},
        {"date": "2026-08-15", "industry": "B", "close": 20.5, "src": "akshare"},
    ]))
    upsert_df(conn, "index_bars", pd.DataFrame([
        {"index_code": "000300", "date": "2026-08-15", "close": 4000.0, "src": "akshare"},
    ]))
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-15", "obj_type": "industry", "obj": "A", "period": "short",
         "rs": 0.1, "rs5": 0.05, "rs10": 0.08, "rs20": 0.12, "trend_stage": "启动", "calc_version": "v1"},
        {"run_date": "2026-08-15", "obj_type": "industry", "obj": "A", "period": "mid",
         "rs": 0.08, "trend_stage": "加速", "calc_version": "v1"},
    ]))
    upsert_df(conn, "macro_series", pd.DataFrame([
        {"indicator": "社会融资规模增量", "date": "2026年04月份", "value": 6245.0, "src": "akshare"},
    ]))
    rows = [{"symbol": "600519", "date": (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat(),
             "close": 100.0, "amount": 1e9, "src": "akshare"} for i in range(120)]
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('600519','core','白酒','2026-08-15')")
    conn.execute("""INSERT INTO cards(symbol, level, cycle, thesis, status, stop_loss, target, created_at)
                    VALUES('600519','A','short','这是一个足够长的投资逻辑说明文本内容','locked', 95.0, 130.0,
                           datetime('now','localtime'))""")
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-15','macro','中性')")
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-15','market','中性')")
    conn.execute("""INSERT INTO viewpoints(source, conclusion, period_tag, confidence, evidence_json,
                   invalid_condition, status, created_at)
                   VALUES('research','测试观点内容','short',0.6,'[]','RS转负','active',datetime('now','localtime'))""")
    conn.commit()


def _fresh_db():
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    return p


# ---------- 注册表完整性 ----------

def test_registry_complete():
    """30 个 skill 全注册：7 报告 + 23 小节；元数据与 uses 引用全合法。"""
    ids = registry.list_skills()
    assert len(ids) == 33, f"期望 33 个 skill，实际 {len(ids)}"
    reports = registry.list_skills("report")
    sections = registry.list_skills("section")
    assert len(reports) == 6
    assert len(sections) == 27
    assert set(reports) == {"a0_premarket", "a3_daily", "a4_weekly",
                            "a5_monthly", "a6_yearly", "b1_intraday"}
    assert registry.validate_all() == []  # 元数据合法 + uses 引用存在


# ---------- 逐字节一致：报告 skill vs 现有 report 函数 ----------

def test_runner_byte_identical_reports():
    """runner.run(aX/b1) 输出与 report.py 原函数逐字节一致。

    a3/a4 内含 LLM 消息面提炼（_news_block 调真实 LLM，输出非确定），
    因此比对时 mock 掉 LLMClient 保证确定性。
    """
    from invest.report import weekly_report

    class _FakeLLM:
        def __init__(self, *a, **k):
            pass

        def run(self, **k):
            return "LLM固定输出（测试用）"

    p = _fresh_db()
    with mock.patch("invest.agent.llm.LLMClient", _FakeLLM):
        assert run_skill("a4_weekly", db_path=p, agent_text="") == weekly_report(p, "")


def test_a3_structured(monkeypatch):
    """a3 盘后日报（2026-08-22 重构）：4 点结构化 + 预案闭环（plan_data）。"""
    import invest.skills.sections._daily_llm as _dl
    from invest.skills.runner import run_structured

    _dl.intraday_review_llm = lambda db, ctx: {
        "verdict": "预测部分正确", "wrong_reasons": ["量能判断失误"],
        "lessons": ["放量日需看承接"]}
    _dl.board_analysis_llm = lambda db, ctx: {
        "boards": [{"name": "AI硬件", "active": True, "analysis": "半导体ETF放量上行，龙头走强",
                    "stock_move": ""},
                   {"name": "机器人", "active": False, "analysis": "横盘待变盘", "stock_move": "某股异动: 消息刺激"}]}
    _dl.plan_gen_llm = lambda db, ctx: {
        "direction": "继续关注AI硬件", "picks": [{"name": "某股", "symbol": "600001",
        "reason": "ETF验证", "plan": "回踩低吸"}],
        "plans": [{"symbol": "600519", "action": "持有"}]}
    _dl.plan_review_llm = lambda db, ctx: {
        "quality": "昨日预案基本兑现", "fixes": ["增加ETF量能权重"]}

    p = _fresh_db()
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35}})
    monkeypatch.setattr("invest.data.etf.fetch_etf_quotes", lambda codes=None: {
        "510300": {"name": "沪深300ETF", "price": 4.0, "pct": 0.5, "amount": 8e9,
                   "turnover": 2.1, "vol_ratio": 1.8, "main_net": 5e8, "super_net": 3e8}})
    monkeypatch.setattr("invest.data.etf.index_etf_signal_text", lambda: "沪深300ETF 量比1.80（明显放量）")
    monkeypatch.setattr("invest.data.etf.sector_etf_text", lambda: "[AI硬件] 半导体ETF +1.2% 成交80亿")
    # 预案历史（质量复盘输入）
    monkeypatch.setattr("invest.skills.reports.a3_daily._plan_history",
                        lambda conn: [{"date": "2026-08-21", "plan_summary": "看多半导体",
                                       "actual_summary": "半导体 +1.2% 兑现"}])

    struct = run_structured("a3_daily", db_path=p)
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    tables = [s for s in struct["sections"] if s.get("type") == "table"]
    assert any(t["title"] == "点1 盘面总览·指数" for t in tables)
    assert any(t["title"] == "点1 指数ETF（量能/资金/大资金进出）" for t in tables)
    assert "点2 盘中观点复盘" in texts and "量能判断失误" in texts
    assert "点3 重要板块总分析" in texts and "AI硬件" in texts and "机器人" in texts
    assert "点4 明日预案" in texts and "600519" in texts
    assert "预案质量复盘" in texts and "增加ETF量能权重" in texts
    # 预案闭环：plan_data 可落库
    assert struct.get("plan_data", {}).get("direction") == "继续关注AI硬件"


def test_b1_structured(monkeypatch):
    """b1 盘中报告（2026-08-22 重构）：结构化输出含 5 节（盘面总览/情绪/主线/核心关注）。"""
    import invest.skills.sections._intraday_llm as _il
    from invest.skills.runner import run_structured

    _il.mood_llm = lambda db, ctx: {
        "mood": "情绪偏暖", "prediction": "滞涨小心回落", "short_term": "短线主升日"}
    _il.mainline_llm = lambda db, ctx: {
        "main_lines": [{"direction": "半导体", "reason": "资金流入",
                        "internal": "小盘强于大盘",
                        "leaders": [{"role": "连板龙头", "name": "某股", "analysis": "放量走强"}],
                        "outlook": "扩圈量能健康"}],
        "core_outlook": "核心标的今日偏强"}

    p = _fresh_db()
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35},
        "000300": {"name": "沪深300", "price": 4618.9, "pct": 0.1},
        "000852": {"name": "中证1000", "price": 7601.8, "pct": 0.9},
    })
    with mock.patch("invest.report._live_quotes", return_value=({"600519": 105.0}, {"600519": 0.05})):
        struct = run_structured("b1_intraday", db_path=p)
    tables = [s for s in struct["sections"] if s.get("type") == "table"]
    charts = [s for s in struct["sections"] if s.get("type") == "chart"]
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert any(t["title"] == "盘面总览" for t in tables)
    assert any(t["title"] == "核心关注实时行情" for t in tables)
    assert charts and charts[0]["chart"] == "index_bars"  # 图表节
    assert "情绪与预测" in texts and "短线主升日" in texts
    assert "日内主线" in texts and "半导体" in texts and "扩圈量能健康" in texts
    assert "核心标的今日偏强" in texts  # 核心关注推演


def test_runner_byte_identical_sections():
    """抽查小节 skill：str 型逐字节一致。"""
    from invest.report import _freshness, _macro_text, _ratings, _strength_block, _temp_guide

    p = _fresh_db()
    conn = connect(p)
    try:
        assert run_skill("d21_freshness", db_path=p) == _freshness(conn)
        assert run_skill("d22_ratings", db_path=p) == _ratings(conn)
        assert run_skill("d6_macro", db_path=p) == _macro_text(conn)
        assert run_skill("d4_strength", db_path=p) == _strength_block(conn, period="short", n=5)
        assert run_skill("d8_temp_guide", score=55.0) == _temp_guide(55.0)
        assert run_skill("d8_temp_guide") == _temp_guide(None)
    finally:
        conn.close()


# ---------- A5/A6：摘要组装（content 传入路径 + 输出片段） ----------

def test_a5_monthly_render():
    """a5_monthly：content 传入与自算结果一致，输出含预期片段。"""
    p = _fresh_db()
    from invest.review.monthly import monthly_review

    conn = connect(p)
    try:
        content = monthly_review(conn)
    finally:
        conn.close()
    out_passed = run_skill("a5_monthly", db_path=p, content=content)
    out_computed = run_skill("a5_monthly", db_path=p)
    assert out_passed == out_computed
    assert out_passed.startswith("月度复盘: 观点命中率")
    assert "待复盘" in out_passed and "环境质量" in out_passed


def test_a6_yearly_render():
    """a6_yearly：content 传入路径输出正确（不依赖 yearly_review 数据）。"""
    p = _fresh_db()
    out = run_skill("a6_yearly", db_path=p, content={"backtest_summary": [1, 2, 3]})
    assert out == "年度复盘已生成: 3 组回测结论待检视"


# ---------- 错误路径 ----------

def test_runner_errors():
    """未知 id / 缺必填 / 多余参数。"""
    with pytest.raises(KeyError):
        run_skill("no_such_skill", db_path="x")
    with pytest.raises(TypeError):
        run_skill("a3_daily")  # 缺 db_path
    with pytest.raises(TypeError):
        run_skill("a3_daily", db_path="x", bogus_param=1)  # 未声明参数
