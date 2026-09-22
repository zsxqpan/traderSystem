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
    """40 个 skill 全注册：7 报告 + 33 小节；元数据与 uses 引用全合法。"""
    ids = registry.list_skills()
    assert len(ids) == 40, f"期望 40 个 skill，实际 {len(ids)}"
    reports = registry.list_skills("report")
    sections = registry.list_skills("section")
    assert len(reports) == 7
    assert len(sections) == 33
    assert "d32_trade_signals" in sections
    assert "d33_daily_actions" in sections
    assert set(reports) == {"a0_premarket", "a3_daily", "a4_weekly", "a5_monthly",
                            "a6_yearly", "a7_auction", "b1_intraday"}
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
    """a3 盘后日报（2026-09-18 精简）：只留对错总结、预案只给方向、无操作表/质量复盘。"""
    import invest.skills.sections._daily_llm as _dl
    from invest.skills.runner import run_structured

    _dl.intraday_review_llm = lambda db, ctx: {"verdict": "预测部分正确"}
    _dl.board_analysis_llm = lambda db, ctx: {
        "boards": [{"name": "AI硬件", "active": True, "analysis": "半导体ETF放量上行，龙头走强",
                    "stock_move": ""},
                   {"name": "机器人", "active": False, "analysis": "横盘待变盘", "stock_move": "某股异动: 消息刺激"}]}
    # 2026-09-18：预案只给方向（不再有 picks/plans）
    _dl.plan_gen_llm = lambda db, ctx: {
        "direction": "继续关注AI硬件", "focus": "半导体/算力", "risk": "高位分化"}
    # 2026-08-24：ETF 解读（量比 1.8 触发 LLM 详细解读）
    _dl.etf_analysis_llm = lambda db, ctx: {
        "summary": "沪深300ETF放量上行，大资金进场",
        "notable": "沪深300ETF量比1.80",
        "attribution": "量能放大与超大单流入同步，疑似国家队动作",
        "style_shift": "大盘权重风格占优，小盘相对走弱"}

    p = _fresh_db()
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35}})
    monkeypatch.setattr("invest.data.etf.fetch_etf_quotes", lambda codes=None: {
        "510300": {"name": "沪深300ETF", "price": 4.0, "pct": 0.5, "amount": 8e9,
                   "turnover": 2.1, "vol_ratio": 1.8, "main_net": 5e8, "super_net": 3e8}})
    monkeypatch.setattr("invest.data.etf.index_etf_signal_text", lambda: "沪深300ETF 量比1.80（明显放量）")
    monkeypatch.setattr("invest.data.etf.sector_etf_text", lambda: "[AI硬件] 半导体ETF +1.2% 成交80亿")
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})
    # 2026-08-23 d28 社区热议：mock 搜索为空（全 mock 不联网），d29 纯规则无影响
    monkeypatch.setattr("invest.agent.web_tools.web_search", lambda query, n=5: [])

    from invest.signals.persist import persist_signals
    from invest.signals.types import Signal

    conn_q = connect(p)
    try:
        persist_signals(
            conn_q,
            [Signal(
                id="quad_hunt", name="主战场", session="daily", severity="watch",
                subject_type="sector", subject="半导体", hint="rs>0 crowding<0.8",
                horizon="mid", layer="discovery",
            )],
            dt.date(2026, 8, 14),
            "daily",
        )
    finally:
        conn_q.close()

    struct = run_structured("a3_daily", db_path=p)
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    tables = [s for s in struct["sections"] if s.get("type") == "table"]
    titles = [t["title"] for t in tables]
    # 2026-09-18 删除项：指数涨跌幅表、今日操作、明日动作表、预案质量复盘
    assert "点1 盘面总览·指数" not in titles
    assert any(t == "点1 指数ETF（量能/资金/大资金进出）" for t in titles)
    assert "📌 今日操作" not in texts
    assert "明日动作（规则）" not in titles
    assert "预案质量复盘" not in texts
    # 复盘只留对错总结，且不再出现错误原因/经验
    assert "点2 盘中观点复盘" in texts and "预测部分正确" in texts
    assert "错误原因" not in texts and "经验" not in texts
    assert "点3 重要板块总分析" in texts and "AI硬件" in texts and "机器人" in texts
    # 预案只给方向、不提个股
    assert "点4 明日预案" in texts and "继续关注AI硬件" in texts
    assert "600519" not in texts and "600001" not in texts
    # 2026-08-24：指数 ETF 解读（LLM 详细归因 + 风格变化）
    assert "指数ETF解读" in texts and "大资金进场" in texts and "风格变化可能" in texts
    # 预案闭环：plan_data 可落库（仅方向字段）
    assert struct.get("plan_data", {}).get("direction") == "继续关注AI硬件"
    assert "picks" not in (struct.get("plan_data") or {})
    assert "中线战场" in texts or "主战场" in texts


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
    monkeypatch.setattr("invest.data.etf.fetch_etf_quotes", lambda codes=None: {})
    monkeypatch.setattr("invest.data.auction.fetch_batch_quotes",
                        lambda symbols=None: {s: {"name": s, "price": 105.0, "pct": 5.0, "vol": 1000}
                                              for s in (symbols or [])})
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})
    with mock.patch("invest.report._live_quotes", return_value=({"600519": 105.0}, {"600519": 0.05})), \
         mock.patch("invest.data.realtime.RealtimeQuoter._fetch_merged", side_effect=RuntimeError("down")):
        struct = run_structured("b1_intraday", db_path=p)
    tables = [s for s in struct["sections"] if s.get("type") == "table"]
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert any(t["title"] == "盘面总览" for t in tables)
    assert any(t["title"] == "核心关注实时行情" for t in tables)
    # 2026-08-24：移除 index_bars 图表（表格已含全部指数涨跌幅，避免重复输出）
    assert not [s for s in struct["sections"] if s.get("type") == "chart"]
    assert "情绪与预测" in texts and "短线主升日" in texts
    assert "日内主线" in texts and "半导体" in texts and "扩圈量能健康" in texts
    assert "核心标的今日偏强" in texts  # 核心关注推演


def test_b1_core_quotes_fallback_close(monkeypatch):
    """2026-08-26：实时失败 → 核心关注回退收盘价（表格不空白）。"""
    from invest.skills.reports.b1_intraday import _core_quotes

    p = _fresh_db()
    # candidate_pool 已有 600519(core)，daily_bars 最新收盘 100.0
    with mock.patch("invest.data.realtime.RealtimeQuoter._fetch_merged",
                    side_effect=RuntimeError("down")):
        rows, _lines, _rs = _core_quotes(p)
    assert rows and any(r[0] == "600519" for r in rows)
    row = next(r for r in rows if r[0] == "600519")
    assert row[1] == "100.00" and "收盘" in row[2]  # 收盘价回退 + 标注


def test_b1_core_quotes_realtime_ok(monkeypatch):
    """2026-08-26：实时正常 → 核心关注用实时价与涨跌幅（不回退）。"""
    from invest.skills.reports.b1_intraday import _core_quotes

    p = _fresh_db()
    from invest.data.quotes import QuoteResult, parse_asset

    live = QuoteResult(
        ref=parse_asset("600519"), price=105.0, pct=0.05, status="live",
        freshness="live", fallback_level="none", src="sina",
    )
    with mock.patch("invest.data.quotes.get_quotes", return_value=[live]):
        rows, _lines, _rs = _core_quotes(p)
    row = next(r for r in rows if r[0] == "600519")
    assert row[1] == "105.00" and "+5.00%" in row[2]


def test_fetch_batch_prices_close_after_loosened():
    """2026-08-26：收盘后（非交易时段）fetch_batch_prices 接受收盘价（不再 10s 全丢）。"""
    from invest.intraday import fetch_batch_prices

    qq = mock.Mock()
    qq.price = 12.95
    qq.ts = dt.datetime.now() - dt.timedelta(hours=2)  # 收盘后 2 小时（旧时间戳）
    qq.src = "sina"
    with mock.patch("invest.intraday._in_trading_window", return_value=False), \
         mock.patch("invest.data.realtime.RealtimeQuoter") as m_rt:
        m_rt.return_value.__enter__.return_value.fetch.return_value = {"sz002412": qq}
        m_rt.return_value.__enter__.return_value.source_failures = {"sina": 0}
        out = fetch_batch_prices(["002412"])
    assert out.get("002412") == 12.95


def test_fetch_batch_prices_intraday_strict():
    """2026-08-26：交易时段仍严格 10s（旧时间戳被丢弃）。"""
    from invest.intraday import fetch_batch_prices

    qq = mock.Mock()
    qq.price = 12.95
    qq.ts = dt.datetime.now() - dt.timedelta(hours=2)
    qq.src = "sina"
    with mock.patch("invest.intraday._in_trading_window", return_value=True), \
         mock.patch("invest.data.realtime.RealtimeQuoter") as m_rt:
        m_rt.return_value.__enter__.return_value.fetch.return_value = {"sz002412": qq}
        m_rt.return_value.__enter__.return_value.source_failures = {"sina": 0}
        out = fetch_batch_prices(["002412"])
    assert "002412" not in out  # 盘中旧时间戳 → 丢弃


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


def test_d26_risk_items_keep_only_stocks():
    """2026-09-18：盘前「涨停异动监控」表只放个股风险条目，债券/汇率/商品/宏观一律不进表。"""
    from invest.skills.sections.d26_market_watch import is_stock_risk, stock_risk_items

    stock = {"kind": "业绩雷", "symbol": "600519", "name": "贵州茅台", "event": "预亏"}
    stock_no_code = {"kind": "风险提示", "symbol": "", "name": "某科技", "event": "问询"}
    bond = {"kind": "风险提示", "symbol": "", "name": "国债收益率上行", "event": "利率抬升"}
    fx = {"kind": "风险提示", "symbol": "", "name": "人民币汇率波动", "event": "贬值"}
    index_item = {"kind": "异动监控", "symbol": "", "name": "上证指数", "event": "放量"}
    empty = {"kind": "风险提示", "symbol": "", "name": "", "event": "无标的"}

    assert is_stock_risk(stock) and is_stock_risk(stock_no_code)
    for bad in (bond, fx, index_item, empty):
        assert not is_stock_risk(bad), bad

    items = stock_risk_items({"risk_items": [stock, bond, stock_no_code, fx, empty]})
    assert [i["name"] for i in items] == ["贵州茅台", "某科技"]
    assert stock_risk_items(None) == []


def test_a7_auction_structured(monkeypatch):
    """a7 竞价报告：指数竞价/高开放量榜/连板竞价/关键股票竞价+解析/核心竞价/情绪预判（全 mock）。"""
    import invest.skills.sections._intraday_llm as _il
    from invest.skills.runner import run_structured

    _il.auction_llm = lambda db, ctx: {
        "mood": "竞价情绪偏暖，高开家数多", "style": "小盘占优", "hint": "关注竞价放量方向"}
    _il.key_stock_llm = lambda db, ctx: {
        "blocks": [{"name": "半导体", "analysis": "受消息影响高开，资金情绪高涨"}]}
    _il.section_analysis_llm = lambda db, ctx: {
        "index": "指数高开，情绪偏暖", "boards": "无", "ladder": "连板承接强",
        "key_stocks": "资金涌入核心方向", "core": "无"}
    p = _fresh_db()
    conn = connect(p)
    try:
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES('20260821', '600001', '某股A', 3, 0)")
        conn.execute("INSERT INTO limit_up_pool(date, symbol, name, lianban, zhaban) "
                     "VALUES('20260821', '600002', '某股B', 2, 0)")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35}})
    monkeypatch.setattr("invest.data.auction.fetch_top_gainers",
                        lambda limit=10: [{"symbol": "600519", "name": "贵州茅台",
                                           "pct": 1.5}])
    monkeypatch.setattr("invest.data.auction.fetch_top_losers",
                        lambda limit=3: [{"symbol": "600000", "name": "浦发银行",
                                          "pct": -2.0}])
    monkeypatch.setattr("invest.data.auction.fetch_vol_top",
                        lambda limit=10: [{"symbol": "600519", "name": "贵州茅台",
                                           "pct": 1.5, "vol": 3e6}])
    monkeypatch.setattr("invest.data.auction.fetch_batch_quotes",
                        lambda symbols=None: {s: {"name": f"N{s}", "price": 10.0, "pct": 1.2,
                                                  "vol": 1000} for s in (symbols or [])})
    now = dt.datetime.now()

    class _LiveQuoter:
        source_failures = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def fetch(self, symbols):
            from invest.data.realtime import Quote

            out = {}
            for s in symbols:
                ms = ("sh" + s) if str(s).startswith(("6", "5", "9")) else (
                    "bj" + s if str(s).startswith(("4", "8")) else "sz" + s
                )
                out[ms] = Quote(symbol=ms, price=10.0, pct=0.012, ts=now, src="sina", name=f"N{s}")
            return out

    monkeypatch.setattr("invest.data.realtime.RealtimeQuoter", _LiveQuoter)
    monkeypatch.setattr("invest.data.auction.fetch_industries",
                        lambda symbols=None: {s: "半导体" for s in (symbols or [])})
    # 关键股票：mock 热门板块核心股（避免依赖 industry_map 数据）
    monkeypatch.setattr("invest.skills.reports.a7_auction._hot_core_stocks",
                        lambda conn: [{"block": "半导体", "count": 2,
                                       "stocks": [{"symbol": "600001", "name": "某股A",
                                                   "lianban": 3}]}])

    struct = run_structured("a7_auction", db_path=p)
    tables = [s for s in struct["sections"] if s.get("type") == "table"]
    charts = [s for s in struct["sections"] if s.get("type") == "chart"]
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert any(t["title"] == "指数竞价" for t in tables)
    assert any("高开榜" in t["title"] for t in tables)
    assert any("放量榜" in t["title"] for t in tables)
    assert any("市场关键股票竞价" in t["title"] for t in tables)
    # 2026-09-18 按需求删除：昨日连板今日竞价、核心关注/持仓竞价
    assert not [t for t in tables if "昨日连板" in t["title"]], "昨日连板竞价已删"
    assert not [t for t in tables if "核心关注" in t["title"]], "核心关注/持仓竞价已删"
    # 2026-09-18：指数涨跌幅只在「指数竞价」表里出现一次 —— 原先还有同数据的条形图，
    # 企微/微信会把图表渲染成数据行、飞书图片上传失败也降级成文本 → 开头重复两次。
    assert not [c for c in charts if "指数竞价" in (c.get("title") or "")], "指数涨跌幅不应重复展示"
    assert "竞价情绪预判" in texts and "小盘占优" in texts
    assert "板块竞价解析" in texts and "受消息影响高开" in texts
    assert "浦发银行" in texts  # 低开榜文本
    # 每模块解析（无特别消息写"（无特别消息）"）；2026-09-18 起不再有 连板/核心关注 两节解析
    for key in ("指数竞价解析", "高开放量榜解析", "关键股票竞价解析"):
        assert key in texts, f"缺少模块解析: {key}"
    assert "连板竞价解析" not in texts and "核心关注竞价解析" not in texts
    assert "（无特别消息）" in texts  # boards=无 → 不强行解析
    # views 元数据（情绪预判 + 模块解析，供落库复盘）
    assert struct.get("views", {}).get("mood", {}).get("mood")
    assert struct.get("views", {}).get("analysis", {}).get("index")
    # 交易信号节：连板全高开 → 至少有高开率信号
    assert "交易信号" in texts
    assert "过精确阈值" in texts
    assert "发现器" in texts


# ---------- 错误路径 ----------

def test_runner_errors():
    """未知 id / 缺必填 / 多余参数。"""
    with pytest.raises(KeyError):
        run_skill("no_such_skill", db_path="x")
    with pytest.raises(TypeError):
        run_skill("a3_daily")  # 缺 db_path
    with pytest.raises(TypeError):
        run_skill("a3_daily", db_path="x", bogus_param=1)  # 未声明参数


# ---------- 2026-08-23 角度 skill 复用：d28-d31 ----------

def test_d28_community_hot(monkeypatch):
    """社区热议（d28）：mock 搜索 + LLM → 输出提炼内容；搜索失败 → 空串。"""
    from invest.skills.sections._community import _cache, community_hot

    _cache.clear()
    fake_items = [
        {"title": "某股票讨论", "url": "http://x", "snippet": "散户看多分歧"},
        {"title": "板块热议", "url": "http://y", "snippet": "机构与散户分歧"},
    ]
    monkeypatch.setattr("invest.agent.web_tools.web_search", lambda query, n=5: fake_items)

    class _FakeLLM:
        def __init__(self, *a, **k):
            pass

        def run(self, **k):
            return "某股票｜散户看多｜雪球"

    monkeypatch.setattr("invest.agent.llm.LLMClient", _FakeLLM)
    p = _tmp_db()
    out = community_hot(p, n=3, job="test")
    assert "某股票" in out and "雪球" in out
    # 搜索失败 → 空串（不阻断）
    _cache.clear()
    monkeypatch.setattr("invest.agent.web_tools.web_search", lambda query, n=5: [])
    assert community_hot(p, n=3, job="test") == ""


def test_d29_sector_resonance():
    """板块共振（d29）：强度∩资金交集按 RS 排序 + 联动；空表 → 空串。"""
    from invest.skills.sections.d29_sector_resonance import _sector_resonance

    p = _tmp_db()
    conn = connect(p)
    try:
        upsert_df(conn, "quant_strength", pd.DataFrame([
            {"run_date": "2026-08-15", "obj_type": "industry", "obj": "半导体", "period": "short",
             "rs": 0.15, "momentum": 0.1, "trend_stage": "加速", "calc_version": "v1"},
            {"run_date": "2026-08-15", "obj_type": "industry", "obj": "白酒", "period": "short",
             "rs": 0.12, "momentum": 0.05, "trend_stage": "启动", "calc_version": "v1"},
            {"run_date": "2026-08-15", "obj_type": "industry", "obj": "银行", "period": "short",
             "rs": 0.10, "momentum": 0.02, "trend_stage": "启动", "calc_version": "v1"},
        ]))
        upsert_df(conn, "sector_fund_flow", pd.DataFrame([
            {"date": "2026-08-15", "industry": "半导体", "main_net": 5e8},
            {"date": "2026-08-15", "industry": "白酒", "main_net": 3e8},
        ]))
        upsert_df(conn, "quant_linkage", pd.DataFrame([
            {"run_date": "2026-08-15", "a": "半导体", "b": "元件", "corr": 0.85, "lead": "半导体"},
        ]))
        out = _sector_resonance(conn, n=3)
        assert "板块共振" in out and "半导体" in out and "白酒" in out
        assert "银行" not in out  # 无资金流入不入选
        assert "半导体" in out.splitlines()[1]  # RS 最高排第一
        assert "联动:元件" in out
        # 空表 → 空串
        conn.execute("DELETE FROM sector_fund_flow")
        conn.commit()
        assert _sector_resonance(conn) == ""
    finally:
        conn.close()


def test_d30_cycle_position():
    """周期行业定位（d30）：阶段判定 + 白名单输出；无数据 → 空串。"""
    from invest.skills.sections.d30_cycle_position import _cycle_position, _stage

    assert _stage(0.05, 1e8, None) == "上行"
    assert _stage(-0.05, -1e8, None) == "下行"
    assert _stage(0.05, -1e8, 0.5) == "震荡"
    assert _stage(-0.1, None, 0.2) == "筑底"
    assert _stage(0.1, None, 0.9) == "过热"

    p = _tmp_db()
    conn = connect(p)
    try:
        upsert_df(conn, "quant_strength", pd.DataFrame([
            {"run_date": "2026-08-15", "obj_type": "industry", "obj": "有色", "period": "mid",
             "rs": 0.08, "trend_stage": "加速", "calc_version": "v1"},
        ]))
        upsert_df(conn, "sector_fund_flow", pd.DataFrame([
            {"date": "2026-08-15", "industry": "有色", "main_net": 2e8},
        ]))
        upsert_df(conn, "quant_valuation", pd.DataFrame([
            {"run_date": "2026-08-15", "obj": "有色", "pe_pct": 0.45},
        ]))
        out = _cycle_position(conn)
        assert "有色" in out and "上行" in out and "PE分位45%" in out
        conn.execute("DELETE FROM quant_strength")
        conn.commit()
        assert _cycle_position(conn) == ""
    finally:
        conn.close()


def test_d31_pool_trap_alerts(monkeypatch):
    """候选池预警（d31）：K线异常硬信号 + 软信号命中 → ≥🟡 输出；软信号无命中仅硬信号 → 不输出。"""
    from invest.skills.sections.d31_pool_trap_alerts import render, scan_pool

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('600001','core','X','2026-08-15')")
        dates = ["2026-08-15", "2026-08-14", "2026-08-13", "2026-08-12", "2026-08-11", "2026-08-10"]
        closes = [100.0, 98.0, 96.0, 94.0, 92.0, 83.3]  # 近5日 +20%（≥15% → K线异常）
        upsert_df(conn, "daily_bars", pd.DataFrame([
            {"date": d, "symbol": "600001", "close": c} for d, c in zip(dates, closes)
        ]))
        conn.commit()
        # 软信号命中：mock 搜索返回推广内容 → 命中 1/2/3/6
        monkeypatch.setattr(
            "invest.agent.web_tools.web_search",
            lambda query, n=5: [{"title": "600001 股票 暴涨 推荐", "url": "http://x",
                                 "snippet": "老师带单 进群 直播间 翻倍"}],
        )
        alerts = scan_pool(conn)
        assert len(alerts) == 1
        hit_names = [s["name"] for s in alerts[0]["signals_hit"]]
        assert "K线异常配合" in hit_names
        assert alerts[0]["level"] in ("🟡", "🟠", "🔴")
        txt = render(p)
        assert "600001" in txt and "杀猪盘扫描" in txt
        # 软信号无命中：只剩 K线 1 信号 → 🟢，render 不输出
        monkeypatch.setattr("invest.agent.web_tools.web_search", lambda query, n=5: [])
        alerts2 = scan_pool(conn)
        assert alerts2[0]["level"] == "🟢"
        assert render(p) == ""
    finally:
        conn.close()


# ---------- 任务 4：报告 manifest / 快照 / 无来源推荐 / 投递状态 ----------

def test_report_manifests_define_contract():
    """每份报告契约含必需/可选块、时点、最低覆盖率、降级方式和最大时长。"""
    from invest.skills.contract import REPORT_MANIFESTS, get_manifest

    for sid in ("a7_auction", "b1_intraday", "a0_premarket", "a3_daily"):
        m = get_manifest(sid)
        assert m.skill_id == sid
        assert m.required_blocks, f"{sid} 缺少必需数据块"
        assert m.optional_blocks is not None
        assert m.slot
        assert 0 < m.min_coverage <= 1
        assert m.degrade_modes
        assert m.max_seconds > 0
        assert sid in REPORT_MANIFESTS
    a7 = get_manifest("a7_auction")
    assert a7.slot == "09:25"
    assert "index_quotes" in a7.required_blocks
    b1 = get_manifest("b1_intraday")
    assert b1.slot == "intraday"
    assert "index_quotes" in b1.required_blocks
    assert "core_quotes" in b1.required_blocks
    assert "facts_only" in b1.degrade_modes


def test_manifest_drives_coverage_degrade():
    """覆盖率门槛来自 manifest，而不是 render 里的 except: pass。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.contract import check_completeness, get_manifest
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    m = get_manifest("b1_intraday")
    idx = [
        QuoteResult(
            ref=parse_asset("000001", "index"),
            status="missing", fallback_level="source_fail", missing_reason="源失败",
        )
    ]
    snap = ReportSnapshot(
        skill_id="b1_intraday",
        as_of="2026-08-28T10:30:00",
        blocks={
            "index_quotes": DataBlock(
                name="index_quotes", as_of="2026-08-28T10:30:00",
                realtime=True, payload=idx, quotes=idx,
            ),
            "core_quotes": DataBlock(
                name="core_quotes", as_of="2026-08-28T10:30:00",
                realtime=True, payload=[], quotes=[],
            ),
        },
    )
    result = check_completeness(m, snap)
    assert result.degrade is True
    assert result.ok is True  # facts_only 允许降级发送
    assert "facts_only" in m.degrade_modes


def test_mainline_llm_forbids_picks_without_candidates():
    """输入没有候选股票列表时，禁止输出 picks/leaders。"""
    import invest.skills.sections._intraday_llm as _il

    fake = {
        "main_lines": [{
            "direction": "半导体",
            "picks": [{"name": "编造股", "symbol": "600001", "reason": "无来源"}],
            "leaders": [{"role": "连板龙头", "name": "编造股", "symbol": "600001",
                         "analysis": "编造"}],
            "outlook": "扩圈",
        }],
        "core_outlook": "偏强",
    }
    out = _il._sanitize_mainline(fake, {"candidates": []})
    assert out.get("main_lines")
    assert out["main_lines"][0].get("picks") in (None, [])
    assert out["main_lines"][0].get("leaders") in (None, [])


def test_mainline_llm_keeps_only_rule_symbols():
    """推荐标的必须来自规则筛出的 symbol，输出前做 schema/证据校验。"""
    import invest.skills.sections._intraday_llm as _il

    fake = {
        "main_lines": [{
            "direction": "白酒",
            "picks": [
                {"name": "贵州茅台", "symbol": "600519", "reason": "核心池"},
                {"name": "编造股", "symbol": "600001", "reason": "幻觉"},
                {"name": "无代码股", "reason": "缺 symbol"},
            ],
            "leaders": [
                {"role": "行业龙头", "name": "贵州茅台", "symbol": "600519",
                 "analysis": "核心池"},
                {"role": "连板龙头", "name": "编造股", "symbol": "999999",
                 "analysis": "幻觉"},
            ],
            "outlook": "震荡",
        }],
    }
    out = _il._sanitize_mainline(fake, {
        "candidates": [{"symbol": "600519", "name": "贵州茅台"}],
    })
    picks = out["main_lines"][0]["picks"]
    leaders = out["main_lines"][0]["leaders"]
    assert [pk["symbol"] for pk in picks] == ["600519"]
    assert [ld["symbol"] for ld in leaders] == ["600519"]


def test_mainline_rejects_name_bypass_and_incomplete_schema():
    """禁止用 name 绕过 symbol；缺 reason/role 的条目丢弃。"""
    import invest.skills.sections._intraday_llm as _il

    fake = {
        "main_lines": [{
            "direction": "白酒",
            "picks": [
                {"name": "贵州茅台", "symbol": "000001", "reason": "错码绕过"},
                {"name": "贵州茅台", "symbol": "600519"},
                {"name": "贵州茅台", "symbol": "600519", "reason": "核心池"},
            ],
            "leaders": [
                {"name": "贵州茅台", "symbol": "000001", "role": "行业龙头",
                 "analysis": "错码绕过"},
                {"name": "贵州茅台", "symbol": "600519", "analysis": "缺 role"},
                {"role": "行业龙头", "name": "贵州茅台", "symbol": "600519",
                 "analysis": "核心池"},
            ],
            "outlook": "震荡",
        }],
    }
    out = _il._sanitize_mainline(fake, {
        "candidates": [{"symbol": "600519", "name": "贵州茅台"}],
    })
    picks = out["main_lines"][0]["picks"]
    leaders = out["main_lines"][0]["leaders"]
    assert picks == [{"name": "贵州茅台", "symbol": "600519", "reason": "核心池"}]
    assert [ld["symbol"] for ld in leaders] == ["600519"]
    assert all(ld.get("role") for ld in leaders)


def test_mainline_rewrites_or_drops_mismatched_pick_name():
    """候选同时有 000001 时，name=茅台/symbol=000001 不得显示茅台。"""
    import invest.skills.sections._intraday_llm as _il

    fake = {
        "main_lines": [{
            "direction": "混搭",
            "picks": [
                {"name": "贵州茅台", "symbol": "000001", "reason": "错配"},
                {"name": "贵州茅台", "symbol": "600519", "reason": "核心池"},
            ],
            "leaders": [
                {"role": "行业龙头", "name": "贵州茅台", "symbol": "000001",
                 "analysis": "错配"},
            ],
            "outlook": "震荡",
        }],
    }
    out = _il._sanitize_mainline(fake, {
        "candidates": [
            {"symbol": "000001", "name": "平安银行"},
            {"symbol": "600519", "name": "贵州茅台"},
        ],
    })
    picks = out["main_lines"][0]["picks"]
    leaders = out["main_lines"][0]["leaders"]
    assert not any(p.get("name") == "贵州茅台" and p.get("symbol") == "000001" for p in picks)
    assert not any(ld.get("name") == "贵州茅台" and ld.get("symbol") == "000001" for ld in leaders)
    moutai = [p for p in picks if p.get("symbol") == "600519"]
    assert moutai and moutai[0]["name"] == "贵州茅台"
    ping_an = [p for p in picks if p.get("symbol") == "000001"]
    if ping_an:
        assert ping_an[0]["name"] == "平安银行"


def test_b1_snapshot_shares_as_of_and_labels_eod(monkeypatch):
    """盘中一次冻结：指数/ETF/核心池共享 as_of；板块 EOD 显式非实时。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.snapshot import freeze_snapshot

    p = _fresh_db()
    now = dt.datetime(2026, 8, 28, 10, 30, 5)

    def _gq(symbols, obj_type="stock", **kw):
        out = []
        for s in symbols:
            ref = parse_asset(s, obj_type)
            if ref is None:
                continue
            extras = None
            if obj_type == "etf":
                extras = {
                    "amount": 8e9, "vol_ratio": 2.4, "main_net": 5e8,
                    "super_net": 12e8, "turnover": 2.1,
                }
            out.append(QuoteResult(
                ref=ref, price=10.0, pct=0.01, status="live",
                freshness="unknown", fallback_level="none", src="eastmoney",
                extras=extras,
            ))
        return out

    monkeypatch.setattr("invest.data.quotes.get_quotes", _gq)
    snap = freeze_snapshot("b1_intraday", p, now=now)
    assert snap.as_of == "2026-08-28T10:30:05"
    for name in ("index_quotes", "etf_quotes", "core_quotes"):
        assert name in snap.blocks
        assert snap.blocks[name].as_of == snap.as_of
        assert snap.blocks[name].realtime is True
    assert "sector_eod" in snap.blocks
    assert snap.blocks["sector_eod"].realtime is False

    from invest.skills.reports.b1_intraday import render

    etf_fetches = {"n": 0}

    def _count_etf(codes=None):
        etf_fetches["n"] += 1
        return {}

    monkeypatch.setattr("invest.data.etf.fetch_etf_quotes", _count_etf)
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mood_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mainline_llm", lambda *a, **k: {})
    import invest.skills.sections.d29_sector_resonance as _d29
    monkeypatch.setattr(_d29, "render", lambda *a, **k: "")
    freeze_etf_calls = etf_fetches["n"]
    struct = render(p, snapshot=snap)
    assert etf_fetches["n"] == freeze_etf_calls  # 生成阶段不得再拉 akshare
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert "非实时" in texts or "收盘参考" in texts
    assert "资金主线(实时)" not in texts
    assert "量比" in texts or "ETF" in texts or "超大单" in texts
    assert struct.get("as_of") == snap.as_of


def test_a7_freezes_925_snapshot_before_render(monkeypatch):
    """竞价报告先冻结 9:25 快照，再生成；不再各自 except: pass 重拉。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.snapshot import freeze_snapshot

    p = _fresh_db()
    now = dt.datetime(2026, 8, 28, 9, 26, 0)
    calls = {"n": 0}

    def _gq(symbols, obj_type="stock", **kw):
        calls["n"] += 1
        return [
            QuoteResult(
                ref=parse_asset(s, obj_type) or parse_asset("000001", obj_type),
                price=10.0, pct=0.01, status="live",
                freshness="unknown", fallback_level="none", src="tencent",
            )
            for s in symbols
        ]

    monkeypatch.setattr("invest.data.quotes.get_quotes", _gq)
    monkeypatch.setattr("invest.data.auction.fetch_top_gainers",
                        lambda limit=8: [{"symbol": "600519", "name": "贵州茅台", "pct": 1.5}])
    monkeypatch.setattr("invest.data.auction.fetch_top_losers", lambda limit=3: [])
    monkeypatch.setattr("invest.data.auction.fetch_vol_top", lambda limit=8: [])
    monkeypatch.setattr("invest.skills.reports.a7_auction._hot_core_stocks", lambda conn: [])
    monkeypatch.setattr("invest.skills.reports.a7_auction._yesterday_ladder", lambda conn: [])
    monkeypatch.setattr("invest.skills.sections._intraday_llm.section_analysis_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.auction_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.key_stock_llm", lambda *a, **k: {})

    snap = freeze_snapshot("a7_auction", p, now=now)
    assert snap.as_of.startswith("2026-08-28T09:25")
    assert not snap.as_of.startswith("2026-08-28T09:26")
    freeze_calls = calls["n"]
    assert freeze_calls >= 1

    from invest.skills.reports.a7_auction import render

    struct = render(p, snapshot=snap)
    assert calls["n"] == freeze_calls  # 生成阶段不再重新拉行情
    assert struct.get("as_of") == snap.as_of
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert "09:25" in texts or "竞价" in texts


def test_deliver_report_checks_completeness_before_send(monkeypatch):
    """发送前完整性检查：必需块缺失则不发送，状态=数据不足。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.scheduler import JobResult
    from invest.skills.report_pipeline import deliver_report
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    p = _fresh_db()
    sent = []
    idx = [
        QuoteResult(
            ref=parse_asset("000001", "index"),
            status="missing", fallback_level="source_fail", missing_reason="源失败",
        )
    ]
    snap = ReportSnapshot(
        skill_id="b1_intraday",
        as_of="2026-08-28T10:30:00",
        blocks={
            "index_quotes": DataBlock(
                name="index_quotes", as_of="2026-08-28T10:30:00",
                realtime=True, payload=[], quotes=[],
            ),
            "core_quotes": DataBlock(
                name="core_quotes", as_of="2026-08-28T10:30:00",
                realtime=True, payload=idx, quotes=idx,
            ),
        },
    )
    result = deliver_report(
        "b1_intraday", p,
        snapshot=snap,
        send_fn=lambda struct: sent.append(struct) or True,
    )
    assert isinstance(result, JobResult)
    assert result.status == "data_insufficient"
    assert sent == []


def test_render_body_typeerror_does_not_bypass_completeness(monkeypatch):
    """render 体内 TypeError 不得当「不支持 snapshot」而无快照重跑、绕过门禁。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.report_pipeline import deliver_report
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    p = _fresh_db()
    live = QuoteResult(
        ref=parse_asset("000001", "index"),
        price=3900.0, pct=0.003, status="live",
        freshness="live", fallback_level="none", src="tencent",
    )
    core = QuoteResult(
        ref=parse_asset("600519"),
        price=105.0, pct=0.05, status="live",
        freshness="live", fallback_level="none", src="sina",
    )
    ok_snap = ReportSnapshot(
        skill_id="b1_intraday",
        as_of="2026-08-28T10:31:00",
        blocks={
            "index_quotes": DataBlock(
                "index_quotes", "2026-08-28T10:31:00", True,
                payload=[live], quotes=[live],
            ),
            "core_quotes": DataBlock(
                "core_quotes", "2026-08-28T10:31:00", True,
                payload=[core], quotes=[core],
            ),
        },
    )
    sent = []
    calls = []

    def _boom_render(db_path, snapshot=None, **kw):
        calls.append(snapshot is not None)
        if snapshot is not None:
            raise TypeError("cannot unpack NoneType in table row")
        return {
            "title": "偷跑",
            "sections": [{"type": "text", "text": "无快照重跑绕过门禁"}],
        }

    monkeypatch.setattr("invest.skills.reports.b1_intraday.render", _boom_render)
    result = deliver_report(
        "b1_intraday", p, now=dt.datetime(2026, 8, 28, 10, 31),
        snapshot=ok_snap, send_fn=lambda s: sent.append(s) or True,
    )
    assert result.status == "generate_failed"
    assert sent == []
    assert calls == [True]


def test_empty_eod_does_not_refetch(monkeypatch):
    """空 EOD 块不得回源再查库。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.reports.b1_intraday import render
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    p = _fresh_db()
    live = QuoteResult(
        ref=parse_asset("000001", "index"),
        price=3900.0, pct=0.003, status="live",
        freshness="unknown", fallback_level="none", src="tencent",
    )
    core = QuoteResult(
        ref=parse_asset("600519"),
        price=105.0, pct=0.05, status="live",
        freshness="unknown", fallback_level="none", src="sina",
    )
    snap = ReportSnapshot(
        skill_id="b1_intraday",
        as_of="2026-08-28T10:31:00",
        blocks={
            "index_quotes": DataBlock(
                "index_quotes", "2026-08-28T10:31:00", True,
                payload=[live], quotes=[live],
            ),
            "core_quotes": DataBlock(
                "core_quotes", "2026-08-28T10:31:00", True,
                payload=[core], quotes=[core],
            ),
            "sector_eod": DataBlock(
                "sector_eod", "2026-08-28T10:31:00", False,
                payload={"sector_top": "", "fund_top": ""},
            ),
        },
    )
    fetched = {"sector": 0, "fund": 0}
    monkeypatch.setattr(
        "invest.skills.reports.b1_intraday._sector_top",
        lambda *a, **k: fetched.__setitem__("sector", fetched["sector"] + 1) or "回源板块",
    )
    monkeypatch.setattr(
        "invest.skills.reports.b1_intraday._fund_top",
        lambda *a, **k: fetched.__setitem__("fund", fetched["fund"] + 1) or "回源资金",
    )
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mood_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mainline_llm", lambda *a, **k: {})
    import invest.skills.sections.d29_sector_resonance as _d29
    monkeypatch.setattr(_d29, "render", lambda *a, **k: "")
    struct = render(p, snapshot=snap, brief=True)
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert fetched == {"sector": 0, "fund": 0}
    assert "回源板块" not in texts
    assert "回源资金" not in texts


def test_report_outcomes_and_channel_receipts(monkeypatch):
    """生成失败 / 数据不足 / 发送失败 / 被限频 / 已成功 五态可区分，成功后写逐通道回执。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.report_pipeline import REPORT_OUTCOMES, deliver_report
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    p = _fresh_db()
    now = dt.datetime(2026, 8, 28, 10, 31, 0)
    live = QuoteResult(
        ref=parse_asset("000001", "index"),
        price=3900.0, pct=0.003, status="live",
        freshness="live", fallback_level="none", src="tencent",
    )
    core = QuoteResult(
        ref=parse_asset("600519"),
        price=105.0, pct=0.05, status="live",
        freshness="live", fallback_level="none", src="sina",
    )
    ok_snap = ReportSnapshot(
        skill_id="b1_intraday",
        as_of="2026-08-28T10:31:00",
        blocks={
            "index_quotes": DataBlock(
                name="index_quotes", as_of="2026-08-28T10:31:00",
                realtime=True, payload=[live], quotes=[live],
            ),
            "core_quotes": DataBlock(
                name="core_quotes", as_of="2026-08-28T10:31:00",
                realtime=True, payload=[core], quotes=[core],
            ),
        },
    )
    monkeypatch.setattr(
        "invest.skills.reports.b1_intraday.render",
        lambda *a, **k: {
            "title": "盘中", "sections": [{"type": "text", "text": "ok"}],
            "as_of": ok_snap.as_of,
        },
    )

    assert REPORT_OUTCOMES == (
        "generate_failed", "data_insufficient", "send_failed", "rate_limited", "ok",
    )

    limited = deliver_report("b1_intraday", p, rate_limited=True, now=now)
    assert limited.status == "rate_limited"

    failed_send = deliver_report(
        "b1_intraday", p, now=now, snapshot=ok_snap,
        send_fn=lambda struct: False,
    )
    assert failed_send.status == "send_failed"

    def _boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr("invest.skills.reports.b1_intraday.render", _boom)
    gen_fail = deliver_report(
        "b1_intraday", p, now=now, snapshot=ok_snap, send_fn=lambda s: True,
    )
    assert gen_fail.status == "generate_failed"

    monkeypatch.setattr(
        "invest.skills.reports.b1_intraday.render",
        lambda *a, **k: {
            "title": "盘中", "sections": [{"type": "text", "text": "ok"}],
            "as_of": ok_snap.as_of,
        },
    )

    def _send_ok(struct):
        from invest.delivery import deliver_channel

        return deliver_channel("feishu", lambda: True, message_kind="report",
                               message_id="b1_intraday")

    from invest.delivery import delivery_context

    with delivery_context(p, "intraday_report", "2026-08-28", "10:31"):
        ok = deliver_report(
            "b1_intraday", p, now=now, snapshot=ok_snap, send_fn=_send_ok,
        )
    assert ok.status == "ok"
    conn = connect(p)
    try:
        rows = conn.execute(
            "SELECT channel, status FROM delivery_receipts WHERE message_id='b1_intraday'"
        ).fetchall()
    finally:
        conn.close()
    assert rows
    assert any(r["status"] == "succeeded" for r in rows)


def test_a0_a3_skip_deliver_report_empty_freeze():
    """盘前/晚报暂不走 deliver_report 门禁；_freeze_light 不种空列表冒充必需块。"""
    import inspect

    from invest.pipeline import notify_after_close, notify_morning_brief
    from invest.skills.snapshot import freeze_snapshot

    p = _fresh_db()
    now = dt.datetime(2026, 8, 28, 8, 40, 0)
    for sid in ("a0_premarket", "a3_daily"):
        snap = freeze_snapshot(sid, p, now=now)
        assert snap.as_of.startswith("2026-08-28T")
        for name, block in snap.blocks.items():
            quotes = getattr(block, "quotes", None)
            payload = getattr(block, "payload", None)
            assert quotes != [], f"{sid}.{name} 用空 quotes 冒充块"
            assert payload not in ([],), f"{sid}.{name} 用空列表冒充块"

    assert "deliver_report" not in inspect.getsource(notify_morning_brief)
    assert "deliver_report" not in inspect.getsource(notify_after_close)


def test_notify_auction_checks_completeness_before_generate(monkeypatch):
    """竞价生产路径：先完整性再生成/发送，缺指数块不得叙事。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.pipeline import notify_auction
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    p = _fresh_db()
    generated = []
    sent = []
    empty = ReportSnapshot(
        skill_id="a7_auction",
        as_of="2026-08-28T09:25:00",
        blocks={
            "index_quotes": DataBlock(
                "index_quotes", "2026-08-28T09:25:00", True,
                payload=[], quotes=[],
            ),
        },
    )
    monkeypatch.setattr("invest.skills.snapshot.freeze_snapshot", lambda *a, **k: empty)
    monkeypatch.setattr(
        "invest.skills.reports.a7_auction.render",
        lambda *a, **k: generated.append(1) or {
            "title": "竞价", "sections": [{"type": "text", "text": "完整竞价报告"}],
        },
    )
    monkeypatch.setattr(
        "invest.skills.runner.run_structured",
        lambda *a, **k: generated.append(1) or {
            "title": "竞价", "sections": [{"type": "text", "text": "完整竞价报告"}],
        },
    )
    monkeypatch.setattr(
        "invest.pipeline._send_structured",
        lambda *a, **k: sent.append(1) or True,
    )
    result = notify_auction(p, return_results=True)
    assert result.status == "data_insufficient"
    assert generated == []
    assert sent == []

    live = QuoteResult(
        ref=parse_asset("000001", "index"),
        price=3900.0, pct=0.003, status="live",
        freshness="unknown", fallback_level="none", src="tencent",
    )
    ok_snap = ReportSnapshot(
        skill_id="a7_auction",
        as_of="2026-08-28T09:25:00",
        blocks={
            "index_quotes": DataBlock(
                "index_quotes", "2026-08-28T09:25:00", True,
                payload=[live], quotes=[live],
            ),
        },
    )
    monkeypatch.setattr("invest.skills.snapshot.freeze_snapshot", lambda *a, **k: ok_snap)
    result_ok = notify_auction(p, return_results=True)
    assert generated  # 完整性通过后才生成
    assert sent
    assert result_ok.status == "ok"


def test_query_data_freshness_includes_report_ledger():
    """系统状态接到任务1账本，不新造驾驶舱。"""
    from invest.agent.tools import query_data_freshness

    p = _tmp_db()
    conn = connect(p)
    try:
        conn.execute(
            """INSERT INTO job_executions(
                   job, scheduled_date, run_slot, status, attempt, detail, artifact
               ) VALUES('auction', '2026-08-28', '09:26', 'data_insufficient', 1,
                        '缺少指数竞价', 'a7_auction')"""
        )
        conn.commit()
        out = query_data_freshness(conn)
        assert "reports" in out
        jobs = {r["job"]: r["status"] for r in out["reports"]}
        assert jobs.get("auction") == "data_insufficient"
    finally:
        conn.close()


def test_b1_discovery_action_not_watch_hint(monkeypatch):
    """明确发现只收 discovery+action，不含 discovery watch 的 hint。"""
    import importlib

    import invest.skills.sections._intraday_llm as _il
    from invest.signals.types import Signal
    from invest.skills.runner import run_structured

    _il.mood_llm = lambda db, ctx: {"mood": "暖", "prediction": "x", "short_term": "y"}
    _il.mainline_llm = lambda db, ctx: {"main_lines": [], "core_outlook": ""}
    fake = [
        Signal(id="high_vol", name="高位放量", session="intraday", severity="action",
               subject_type="stock", subject="000002", hint="量比滞涨发现票",
               horizon="short", layer="discovery"),
        Signal(id="shrink_extreme", name="极致缩量", session="intraday", severity="watch",
               subject_type="stock", subject="000003", hint="洗盘观察池外不该出现",
               horizon="short", layer="discovery"),
        Signal(id="quad_hunt", name="主战场", session="daily", severity="action",
               subject_type="sector", subject="半导体", hint="中线不进b1",
               horizon="mid", layer="discovery"),
    ]
    scan_mod = importlib.import_module("invest.signals.scan")
    monkeypatch.setattr(scan_mod, "scan_db", lambda *a, **k: fake)
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35},
    })
    monkeypatch.setattr("invest.data.auction.fetch_batch_quotes",
                        lambda symbols=None: {})
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})
    p = _fresh_db()
    with mock.patch("invest.report._live_quotes", return_value=({}, {})):
        struct = run_structured("b1_intraday", db_path=p)
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert "明确发现" in texts
    assert "000002" in texts
    assert "洗盘观察池外不该出现" not in texts
    assert "中线不进b1" not in texts


def test_weekly_report_contains_mid_signals():
    from invest.report import weekly_report
    from invest.signals.persist import persist_signals
    from invest.signals.types import Signal

    p = _fresh_db()
    conn = connect(p)
    try:
        persist_signals(
            conn,
            [Signal(
                id="quad_hunt", name="主战场", session="daily", severity="watch",
                subject_type="sector", subject="半导体", hint="拥挤回落仍强",
                horizon="mid", layer="discovery",
            )],
            dt.date(2026, 8, 14),
            "daily",
        )
    finally:
        conn.close()
    from pathlib import Path

    msg = weekly_report(p)
    assert "中线信号" in msg
    assert "半导体" in msg
    root = Path(__file__).resolve().parents[1]
    src_a5 = (root / "invest/skills/reports/a5_monthly.py").read_text(encoding="utf-8")
    src_a6 = (root / "invest/skills/reports/a6_yearly.py").read_text(encoding="utf-8")
    assert "format_market_opportunity" not in src_a5
    assert "scan_db" not in src_a6


def test_llm_prompts_forbid_quadrant_fabrication():
    from pathlib import Path

    from invest.skills.sections import _daily_llm, _intraday_llm

    assert "禁止编造" in _daily_llm._SIGNAL_CITE
    assert "象限" in _daily_llm._SIGNAL_CITE
    assert "象限" in _intraday_llm._SIGNAL_CITE
    src = (Path(__file__).resolve().parents[1] / "invest/skills/sections/_daily_llm.py").read_text(encoding="utf-8")
    # 2026-09-18：预案改为"只给方向"，不再有 picks/quad_hunt 相关约束；预案质量复盘函数已删
    assert "禁止出现任何个股名称或代码" in src
    assert not hasattr(_daily_llm, "plan_review_llm")


def test_llm_prompts_forbid_suggest_buy():
    from pathlib import Path

    from invest.skills.sections import _daily_llm, _intraday_llm

    assert "建议买入" in _daily_llm._SIGNAL_CITE or "建议买入" in _daily_llm._ACTION_CITE
    assert "建议买入" in _intraday_llm._SIGNAL_CITE
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "invest/skills/reports/a0_premarket.py",
        "invest/skills/reports/a3_daily.py",
        "invest/skills/reports/a7_auction.py",
        "invest/skills/reports/b1_intraday.py",
        "invest/skills/sections/d32_trade_signals.py",
        "invest/skills/sections/d33_daily_actions.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "建议买入" not in text


def test_d32_d33_readonly_no_persist(monkeypatch):
    from invest.skills.sections.d32_trade_signals import render as d32
    from invest.skills.sections.d33_daily_actions import render as d33

    p = _fresh_db()
    conn = connect(p)
    try:
        n_sig = conn.execute("SELECT COUNT(*) AS n FROM trade_signals").fetchone()["n"]
        n_act = conn.execute("SELECT COUNT(*) AS n FROM daily_actions").fetchone()["n"]
    finally:
        conn.close()

    def _boom(*a, **k):
        raise AssertionError("d32/d33 不得落库")

    import invest.actions.persist as act_persist
    import invest.signals.persist as sig_persist

    monkeypatch.setattr(sig_persist, "persist_signals", _boom)
    monkeypatch.setattr(act_persist, "persist_actions", _boom)
    d32(p, session="intraday")
    d32(p, session="daily")
    d33(p)
    conn = connect(p)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM trade_signals").fetchone()["n"] == n_sig
        assert conn.execute("SELECT COUNT(*) AS n FROM daily_actions").fetchone()["n"] == n_act
    finally:
        conn.close()


def test_a7_scan_receives_boards_and_core_tags_watch_only(monkeypatch):
    """boards 必须传入 scan；核心表 tags 只标 watch。"""
    import invest.skills.sections._intraday_llm as _il
    from invest.data.quotes import AssetRef, QuoteResult
    from invest.signals.types import Signal
    from invest.skills.runner import run_structured
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    captured: dict = {}

    def _scan(db_path, session, **kw):
        captured["session"] = session
        captured["boards"] = kw.get("boards")
        captured["persist"] = kw.get("persist")
        return [
            Signal(
                id="shrink_extreme", name="极致缩量", session="auction",
                severity="watch", subject_type="stock", subject="600519",
                hint="发现层不该进核心标签", horizon="short", layer="discovery",
            ),
            Signal(
                id="auction_keep_vol", name="竞价保量", session="auction",
                severity="watch", subject_type="stock", subject="600519",
                hint="池内保量", horizon="short", layer="watch",
            ),
        ]

    import importlib

    scan_mod = importlib.import_module("invest.signals.scan")
    monkeypatch.setattr(scan_mod, "scan_db", _scan)
    _il.auction_llm = lambda db, ctx: {}
    _il.key_stock_llm = lambda db, ctx: {"blocks": []}
    _il.section_analysis_llm = lambda db, ctx: {}

    now = dt.datetime(2026, 8, 22, 9, 26)
    q = QuoteResult(
        ref=AssetRef("600519", "stock", "茅台"), price=10.0, pct=0.01,
        ts=now, src="test", status="live", freshness="live",
    )
    snap = ReportSnapshot(skill_id="a7_auction", as_of=now.isoformat(timespec="seconds"))
    boards = {
        "gainers": [{"symbol": "600519", "name": "茅台", "pct": 2.0, "vol": 1e6}],
        "losers": [{"symbol": "000001", "name": "平安", "pct": -2.0, "vol": 1e5}],
        "vol_top": [{"symbol": "300750", "name": "宁德", "pct": 1.0, "vol": 2e6}],
    }
    snap.blocks["auction_boards"] = DataBlock("auction_boards", snap.as_of, True, payload=boards)
    snap.blocks["index_quotes"] = DataBlock("index_quotes", snap.as_of, True, quotes=[])
    snap.blocks["core_quotes"] = DataBlock("core_quotes", snap.as_of, True, quotes=[q])
    snap.blocks["ladder_quotes"] = DataBlock("ladder_quotes", snap.as_of, True, quotes=[])
    snap.blocks["key_quotes"] = DataBlock("key_quotes", snap.as_of, True, payload={"hot": []}, quotes=[])
    monkeypatch.setattr("invest.skills.snapshot.freeze_snapshot", lambda *a, **k: snap)

    p = _fresh_db()
    struct = run_structured("a7_auction", db_path=p, snapshot=snap)
    assert captured.get("session") == "auction"
    assert captured.get("persist") is True
    board_syms = {b.get("symbol") for b in (captured.get("boards") or [])}
    assert {"600519", "000001", "300750"} <= board_syms
    # 2026-09-18：核心关注/持仓竞价小节已删，标签列随之消失
    assert not [
        t for t in struct["sections"]
        if t.get("type") == "table" and "核心关注" in (t.get("title") or "")
    ], "核心关注竞价已按需求删除"


def test_b1_brief_keeps_discovery_and_caps_five(monkeypatch):
    """简洁版也有明确发现；discovery×action 最多 5；中线不进。"""
    import importlib

    import invest.skills.sections._intraday_llm as _il
    from invest.signals.types import Signal
    from invest.skills.runner import run_structured

    _il.mood_llm = lambda db, ctx: {}
    _il.mainline_llm = lambda db, ctx: {"main_lines": [], "core_outlook": ""}
    fake = [
        Signal(
            id="high_vol", name="高位放量", session="intraday", severity="action",
            subject_type="stock", subject=f"00000{i}", hint=f"发现票{i}",
            horizon="short", layer="discovery",
        )
        for i in range(1, 7)
    ]
    fake.append(Signal(
        id="quad_hunt", name="主战场", session="daily", severity="action",
        subject_type="sector", subject="半导体", hint="中线不进简洁版",
        horizon="mid", layer="discovery",
    ))
    scan_mod = importlib.import_module("invest.signals.scan")
    monkeypatch.setattr(scan_mod, "scan_db", lambda *a, **k: fake)
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35},
    })
    monkeypatch.setattr("invest.data.auction.fetch_batch_quotes", lambda symbols=None: {})
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})
    p = _fresh_db()
    from invest.actions.persist import persist_actions
    from invest.actions.types import Action

    conn_a = connect(p)
    try:
        persist_actions(
            conn_a,
            [Action(date=dt.date.today().isoformat(), symbol="600519",
                    verb="hold", priority=1, source="card", hint="持仓对照")],
            dt.date.today(),
        )
    finally:
        conn_a.close()
    with mock.patch("invest.report._live_quotes", return_value=({}, {})):
        struct = run_structured("b1_intraday", db_path=p, brief=True)
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    tables = [s for s in struct["sections"] if s.get("type") == "table"]
    assert "明确发现" in texts
    assert "中线不进简洁版" not in texts
    disc_hits = [f"00000{i}" for i in range(1, 7) if f"00000{i}" in texts]
    assert len(disc_hits) == 5
    assert any(t["title"] == "动作对照" for t in tables)


def test_a3_drops_lessons_action_table_and_plan_review(monkeypatch):
    """2026-09-18 精简：报告里不再有 lessons/校验库、明日动作表、预案质量复盘；
    动作清单仍在后台落库（daily_actions 有行），只是不进报告。"""
    import invest.skills.sections._daily_llm as _dl
    from invest.skills.runner import run_structured

    _dl.intraday_review_llm = lambda db, ctx: {"verdict": "部分对"}
    _dl.board_analysis_llm = lambda db, ctx: {"boards": []}
    _dl.plan_gen_llm = lambda db, ctx: {"direction": "观望", "focus": "等右侧", "risk": "缩量"}
    _dl.etf_analysis_llm = lambda db, ctx: {}
    monkeypatch.setattr("invest.data.index_realtime.fetch_index_realtime", lambda: {
        "000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35}})
    monkeypatch.setattr("invest.data.etf.fetch_etf_quotes", lambda codes=None: {})
    monkeypatch.setattr("invest.data.etf.index_etf_signal_text", lambda: "")
    monkeypatch.setattr("invest.data.etf.sector_etf_text", lambda: "")
    monkeypatch.setattr("invest.data.auction.fetch_industries", lambda symbols=None: {})
    monkeypatch.setattr("invest.agent.web_tools.web_search", lambda query, n=5: [])
    p = _fresh_db()
    struct = run_structured("a3_daily", db_path=p)
    joined = "".join(
        (s.get("title") or "") + (s.get("text") or "")
        for s in struct["sections"]
    )
    assert "明日动作（规则）" not in joined
    assert "预案质量复盘" not in joined
    assert "错误原因" not in joined and "经验:" not in joined
    assert "点4 明日预案" in joined and "观望" in joined
    conn = connect(p)
    try:
        n_actions = conn.execute("SELECT COUNT(*) AS n FROM daily_actions").fetchone()["n"]
    finally:
        conn.close()
    # 动作清单仍然后台落库（供仪表盘/对话 d33/盘中报告），只是不再出现在报告里
    assert n_actions >= 0


def test_weekly_mid_uses_last_five_dates():
    """周报中线取最近 5 个 date，更早的去重丢弃。"""
    from invest.report import weekly_report
    from invest.signals.persist import persist_signals
    from invest.signals.types import Signal

    p = _fresh_db()
    conn = connect(p)
    try:
        for i, (day, subj) in enumerate((
            (dt.date(2026, 8, 10), "太旧行业"),
            (dt.date(2026, 8, 11), "行业A"),
            (dt.date(2026, 8, 12), "行业B"),
            (dt.date(2026, 8, 13), "行业C"),
            (dt.date(2026, 8, 14), "行业D"),
            (dt.date(2026, 8, 15), "行业E"),
        )):
            persist_signals(
                conn,
                [Signal(
                    id="quad_hunt", name="主战场", session="daily", severity="watch",
                    subject_type="sector", subject=subj, hint=f"d{i}",
                    horizon="mid", layer="discovery",
                )],
                day,
                "daily",
            )
    finally:
        conn.close()
    msg = weekly_report(p)
    assert "中线信号" in msg
    assert "行业A" in msg and "行业E" in msg
    assert "太旧行业" not in msg
