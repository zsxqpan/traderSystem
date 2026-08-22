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
    assert len(ids) == 30, f"期望 30 个 skill，实际 {len(ids)}"
    reports = registry.list_skills("report")
    sections = registry.list_skills("section")
    assert len(reports) == 7
    assert len(sections) == 23
    assert set(reports) == {"a1_premarket", "a2_morning_brief", "a3_daily", "a4_weekly",
                            "a5_monthly", "a6_yearly", "b1_intraday"}
    assert registry.validate_all() == []  # 元数据合法 + uses 引用存在


# ---------- 逐字节一致：报告 skill vs 现有 report 函数 ----------

def test_runner_byte_identical_reports():
    """runner.run(aX/b1) 输出与 report.py 原函数逐字节一致。

    a3/a4 内含 LLM 消息面提炼（_news_block 调真实 LLM，输出非确定），
    因此比对时 mock 掉 LLMClient 保证确定性。
    """
    from invest.report import daily_report, intraday_report, morning_brief_report, premarket_report, weekly_report

    class _FakeLLM:
        def __init__(self, *a, **k):
            pass

        def run(self, **k):
            return "LLM固定输出（测试用）"

    p = _fresh_db()
    assert run_skill("a1_premarket", db_path=p, agent_text="关注方向测试") == premarket_report(p, "关注方向测试")
    assert run_skill("a2_morning_brief", db_path=p) == morning_brief_report(p)
    with mock.patch("invest.agent.llm.LLMClient", _FakeLLM):
        assert run_skill("a3_daily", db_path=p, agent_text="") == daily_report(p, "")
        assert run_skill("a4_weekly", db_path=p, agent_text="") == weekly_report(p, "")
    with mock.patch("invest.report._live_quotes", return_value=({"600519": 105.0}, {"600519": 0.05})):
        assert run_skill("b1_intraday", db_path=p, public=True, brief=False) == \
            intraday_report(p, public=True, brief=False)


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
        run_skill("a1_premarket")  # 缺 db_path
    with pytest.raises(TypeError):
        run_skill("a1_premarket", db_path="x", bogus_param=1)  # 未声明参数
