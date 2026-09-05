"""统一行情契约（任务 2）：规范化 / 三源合并消费 / 新鲜度一致 / 覆盖率降级。

全 mock，不连真实网络。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.data.realtime import Quote
from invest.db import connect, init_db


def _tmp_db(name: str = "invest_quotes_test.db"):
    p = os.path.join(tempfile.gettempdir(), name)
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _q(symbol: str, price=10.0, pct=0.01, ts=None, src="sina"):
    return Quote(
        symbol=symbol,
        price=price,
        pct=pct,
        ts=ts or dt.datetime.now(),
        src=src,
    )


# ---------- 1) 代码规范化 / 名称解析 ----------

def test_normalize_stock_index_etf_bj():
    from invest.data.quotes import market_symbol, normalize_symbol, parse_asset, resolve_name

    assert normalize_symbol("600519") == "600519"
    assert normalize_symbol("600519.SH") == "600519"
    assert normalize_symbol("sh600519") == "600519"
    assert normalize_symbol(" 000001 ") == "000001"
    assert normalize_symbol("830001") == "830001"
    assert normalize_symbol("abc") == ""
    assert normalize_symbol("60051") == ""
    assert normalize_symbol("") == ""

    assert market_symbol("600519") == "sh600519"
    assert market_symbol("000001", "stock") == "sz000001"
    assert market_symbol("000001", "index") == "sh000001"
    assert market_symbol("399001", "index") == "sz399001"
    assert market_symbol("899050", "index") == "bj899050"
    assert market_symbol("510300", "etf") == "sh510300"
    assert market_symbol("159915", "etf") == "sz159915"
    assert market_symbol("830001") == "bj830001"
    assert market_symbol("430047") == "bj430047"

    ref = parse_asset("600519.SH")
    assert ref is not None and ref.symbol == "600519" and ref.obj_type == "stock"
    assert parse_asset("not-a-code") is None
    assert resolve_name("000001", "index") == "上证指数"
    assert resolve_name("510300", "etf") == "沪深300ETF"


def test_pool_rejects_illegal_or_unnormalized():
    from invest.discipline.pool import add_to_pool

    p = _tmp_db("invest_quotes_pool.db")
    conn = connect(p)
    try:
        with pytest.raises(ValueError, match="非法|规范"):
            add_to_pool(conn, "C000")
        with pytest.raises(ValueError, match="非法|规范"):
            add_to_pool(conn, "X1")
        with pytest.raises(ValueError, match="非法|规范"):
            add_to_pool(conn, "")
        r = add_to_pool(conn, "600519.SH", level="track")
        assert r["symbol"] == "600519"
        row = conn.execute("SELECT symbol FROM candidate_pool WHERE symbol='600519'").fetchone()
        assert row is not None
    finally:
        conn.close()


# ---------- 2) 请求标的全量覆盖 + 回退 ----------

def test_requested_symbols_never_silently_dropped():
    from invest.data.quotes import get_quotes

    p = _tmp_db()
    conn = connect(p)
    conn.execute(
        "INSERT INTO daily_bars(symbol, date, close, src) VALUES('600519','2026-08-27',100.0,'akshare')"
    )
    conn.commit()
    conn.close()

    def _sina(session, symbols):
        return {"sh600519": _q("sh600519", price=105.0, pct=0.05, src="sina")}

    with mock.patch("invest.data.realtime._fetch_sina", side_effect=_sina), \
         mock.patch("invest.data.realtime._fetch_tencent", return_value={}), \
         mock.patch("invest.data.realtime._fetch_em", return_value={}):
        results = get_quotes(["600519", "000001", "830001"], obj_type="stock", db_path=p)

    assert [r.ref.symbol for r in results] == ["600519", "000001", "830001"]
    by = {r.ref.symbol: r for r in results}
    assert by["600519"].status == "live"
    assert by["600519"].price == 105.0
    assert by["000001"].status == "missing"
    assert by["000001"].fallback_level in ("no_history", "source_fail")
    assert by["000001"].missing_reason
    assert by["830001"].status == "missing"


def test_fallback_last_close_and_no_history():
    from invest.data.quotes import get_quotes

    p = _tmp_db()
    conn = connect(p)
    conn.execute(
        "INSERT INTO daily_bars(symbol, date, close, src) VALUES('600519','2026-08-27',1888.0,'akshare')"
    )
    conn.commit()
    conn.close()

    with mock.patch("invest.data.realtime.RealtimeQuoter._fetch_merged",
                    side_effect=RuntimeError("all down")):
        results = get_quotes(["600519", "000002"], obj_type="stock", db_path=p)
    by = {r.ref.symbol: r for r in results}
    assert by["600519"].status == "fallback"
    assert by["600519"].fallback_level == "last_close"
    assert by["600519"].price == 1888.0
    assert by["000002"].status == "missing"
    assert by["000002"].fallback_level in ("no_history", "source_fail")


def _sina_suspended_line() -> str:
    """新浪停牌行：最新价=0，昨收>0，名称非空 → 解析层标 suspended。"""
    # 字段位：0名称 1今开 2昨收 3最新 … 30日期 31时间
    segs = ["0"] * 33
    segs[0] = "浦发银行"
    segs[1] = "0.000"
    segs[2] = "10.500"
    segs[3] = "0.000"
    segs[30] = "2026-08-28"
    segs[31] = "09:30:00"
    return 'var hq_str_sh600000="' + ",".join(segs) + '";'


def test_get_quotes_classifies_suspended_via_real_fetch_path():
    """停牌必须走 fetch_resolved/_fetch_merged：fetch() 会丢掉 price<=0。

    源层返回停牌 Quote，再经 RealtimeQuoter 合并 → get_quotes 分类。
    库里有昨收，禁止误判成「最近收盘」或「缺历史」。
    """
    from invest.data.quotes import get_quotes, status_label
    from invest.data.realtime import _parse_sina_line

    parsed = _parse_sina_line(_sina_suspended_line())
    assert parsed is not None
    assert parsed.price in (None, 0.0)
    assert parsed.name == "浦发银行"
    assert parsed.suspended is False  # 无价 ≠ 立刻停牌；换源耗尽后才标停牌

    p = _tmp_db("invest_quotes_suspend_path.db")
    conn = connect(p)
    conn.execute(
        "INSERT INTO daily_bars(symbol, date, close, src) VALUES('600000','2026-08-27',10.5,'akshare')"
    )
    conn.commit()
    conn.close()

    def _sina(session, symbols):
        return {"sh600000": parsed}

    with mock.patch("invest.data.realtime._fetch_sina", side_effect=_sina), \
         mock.patch("invest.data.realtime._fetch_tencent", return_value={}), \
         mock.patch("invest.data.realtime._fetch_em", return_value={}):
        # 对照：fetch() 丢停牌（price<=0）
        from invest.data.realtime import RealtimeQuoter

        with RealtimeQuoter() as qrt:
            try:
                priced = qrt.fetch(["600000"])
            except RuntimeError:
                priced = {}
            assert not any(getattr(v, "suspended", False) for v in priced.values())
            # 真实分类路径：合并层保留停牌 → get_quotes
            results = get_quotes(["600000"], obj_type="stock", db_path=p, quoter=qrt)
    by = {r.ref.symbol: r for r in results}
    assert by["600000"].fallback_level == "suspended"
    assert status_label(by["600000"]) == "停牌"
    assert status_label(by["600000"]) != "最近收盘"


def test_status_label_distinguishes_all_states():
    from invest.data.quotes import QuoteResult, parse_asset, status_label

    live = QuoteResult(
        ref=parse_asset("600519"), price=100.0, freshness="live",
        fallback_level="none", status="live", src="sina",
    )
    stale = QuoteResult(
        ref=parse_asset("000001"), price=10.0, freshness="stale",
        fallback_level="none", status="fallback", src="sina",
    )
    close = QuoteResult(
        ref=parse_asset("000002"), price=9.0, freshness="unknown",
        fallback_level="last_close", status="fallback", src="daily_bars",
    )
    susp = QuoteResult(
        ref=parse_asset("600000"), price=10.0, fallback_level="suspended",
        missing_reason="停牌", status="fallback",
    )
    nohist = QuoteResult(
        ref=parse_asset("830001"), status="missing", fallback_level="no_history",
        missing_reason="缺历史",
    )
    fail = QuoteResult(
        ref=parse_asset("000003"), status="missing", fallback_level="source_fail",
        missing_reason="源失败",
    )
    assert status_label(live) == "实时"
    assert status_label(stale) == "过期实时"
    assert status_label(close) == "最近收盘"
    assert status_label(susp) == "停牌"
    assert status_label(nohist) == "缺历史"
    assert status_label(fail) == "源失败"


def test_stale_realtime_not_labeled_as_last_close():
    """过期实时价不得伪装成最近收盘。"""
    from invest.data.quotes import get_quotes, status_label

    now = dt.datetime(2026, 8, 28, 10, 0, 0)
    stale = _q("sz000001", price=10.0, ts=now - dt.timedelta(seconds=90), src="sina")

    def _sina(session, symbols):
        return {"sz000001": stale}

    with mock.patch("invest.data.realtime._fetch_sina", side_effect=_sina), \
         mock.patch("invest.data.realtime._fetch_tencent", return_value={}), \
         mock.patch("invest.data.realtime._fetch_em", return_value={}):
        results = get_quotes(["000001"], obj_type="stock", now=now)
    r = results[0]
    assert r.freshness == "stale"
    assert r.fallback_level != "last_close"
    assert status_label(r) == "过期实时"
    assert status_label(r) != "最近收盘"


def test_index_etf_quote_no_fake_live_clock():
    """指数/ETF：freshness 不恒 live；尽量填 prev_close；无行情钟则 ts 空。"""
    from invest.data.quotes import get_quotes

    with mock.patch("invest.data.index_realtime.fetch_index_realtime", return_value={
        "000001": {"name": "上证指数", "price": 3900.0, "pct": 0.35},
    }):
        idx = get_quotes(["000001"], obj_type="index")
    r = idx[0]
    assert r.price == 3900.0
    assert r.prev_close is not None
    assert abs(r.prev_close - 3900.0 / 1.0035) < 0.02
    assert r.ts is None
    assert r.freshness == "unknown"

    with mock.patch("invest.data.etf.fetch_etf_quotes", return_value={
        "510300": {"name": "沪深300ETF", "price": 4.0, "pct": 0.5},
    }):
        etf = get_quotes(["510300"], obj_type="etf")
    e = etf[0]
    assert e.price == 4.0
    assert e.prev_close is not None
    assert abs(e.prev_close - 4.0 / 1.005) < 0.01
    assert e.ts is None
    assert e.freshness == "unknown"


def test_per_symbol_freshness():
    from invest.data.quotes import get_quotes
    from invest.data.realtime import RealtimeQuoter

    now = dt.datetime(2026, 8, 28, 10, 0, 0)
    live = _q("sh600519", price=100.0, ts=now, src="sina")
    stale = _q("sz000001", price=10.0, ts=now - dt.timedelta(seconds=90), src="sina")

    def _merged(self, symbols):
        return {"600519": live, "000001": stale}, None

    with mock.patch.object(RealtimeQuoter, "_fetch_merged", _merged):
        results = get_quotes(["600519", "000001"], obj_type="stock", now=now)
    by = {r.ref.symbol: r for r in results}
    assert by["600519"].freshness == "live"
    assert by["600519"].status == "live"
    assert by["000001"].freshness == "stale"


def test_beijing_exchange_in_contract():
    from invest.data.quotes import get_quotes, market_symbol
    from invest.data.realtime import RealtimeQuoter

    assert market_symbol("830001") == "bj830001"
    q = _q("bj830001", price=8.8, src="em_push2")

    def _merged(self, symbols):
        assert any(str(s).endswith("830001") for s in symbols)
        return {"830001": q}, None

    with mock.patch.object(RealtimeQuoter, "_fetch_merged", _merged):
        results = get_quotes(["830001"])
    assert results[0].ref.symbol == "830001"
    assert results[0].status == "live"
    assert results[0].src == "em_push2"


# ---------- 3) Agent 工具消费统一契约 ----------

def test_query_realtime_quote_uses_unified_contract():
    from invest.agent.tools import query_realtime_quote
    from invest.data.quotes import QuoteResult, parse_asset

    p = _tmp_db()
    conn = connect(p)
    live = QuoteResult(
        ref=parse_asset("600519"),
        price=1304.66, prev_close=1272.0, pct=0.025,
        ts=dt.datetime(2026, 8, 25, 10, 0), src="sina",
        freshness="live", fallback_level="none", status="live",
    )
    miss = QuoteResult(
        ref=parse_asset("000001"),
        status="missing", fallback_level="source_fail", missing_reason="三源均无报价",
    )
    with mock.patch("invest.data.quotes.get_quotes", return_value=[live, miss]):
        out = query_realtime_quote(conn, symbols=["600519", "000001"])
    assert out["quotes"]["600519"]["price"] == 1304.66
    assert out["quotes"]["600519"]["status"] == "live"
    assert out["quotes"]["600519"]["freshness"] == "live"
    assert "prev_close" in out["quotes"]["600519"]
    assert out["quotes"]["000001"]["status"] == "missing"
    assert out["coverage"]["requested"] == 2
    assert out["coverage"]["missing"] == 1
    conn.close()


# ---------- 4) freshness 判定一致 + 实时 probe ----------

def test_freshness_verdict_consistent_off_hours():
    from invest.agent.tools import evaluate_freshness, freshness_gate, query_data_freshness
    from invest.data.calendar import latest_trading_day

    p = _tmp_db()
    conn = connect(p)
    exp = latest_trading_day(dt.date.today()).isoformat()
    with mock.patch("invest.intraday._in_trading_window", return_value=False):
        ev = evaluate_freshness(conn)
        ok, reason = freshness_gate(conn)
        q = query_data_freshness(conn)
    assert ev["fresh"] is False
    assert ok is False and "数据截至" in reason
    assert q["fresh"] is ev["fresh"]
    assert q["stale_parts"] == ev["stale_parts"]

    conn.execute("INSERT INTO daily_bars(symbol, date, close, src) VALUES('600519',?,100,'akshare')", (exp,))
    conn.commit()
    from invest.agent.tools import _fresh_cache
    _fresh_cache.clear()
    with mock.patch("invest.intraday._in_trading_window", return_value=False):
        ev2 = evaluate_freshness(conn)
        ok2, _ = freshness_gate(conn)
        q2 = query_data_freshness(conn)
    assert ev2["fresh"] is True and ok2 is True and q2["fresh"] is True
    conn.close()


def test_freshness_trading_uses_probe_not_job_runs():
    from invest.agent.tools import evaluate_freshness, freshness_gate, query_data_freshness
    from invest.data.quotes import QuoteResult, parse_asset

    p = _tmp_db()
    conn = connect(p)
    # 旧 job_runs.realtime 留痕：按旧逻辑会判不可用
    conn.execute(
        """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
           VALUES('realtime','ok',datetime('now','localtime'),datetime('now','localtime'),
                  'src=sina n=1 stale=9 failures={}')"""
    )
    conn.commit()
    live = QuoteResult(
        ref=parse_asset("600519"), price=100.0, pct=0.01,
        ts=dt.datetime.now(), src="sina", freshness="live", status="live",
        fallback_level="none",
    )
    with mock.patch("invest.intraday._in_trading_window", return_value=True), \
         mock.patch("invest.data.quotes.probe_realtime",
                    return_value={"ok": True, "live": 1, "requested": 1, "detail": "probe live"}):
        ev = evaluate_freshness(conn)
        ok, reason = freshness_gate(conn)
        q = query_data_freshness(conn)
    assert ev["fresh"] is True and ev["realtime_ok"] is True
    assert ok is True
    assert q["fresh"] is True and q["realtime_ok"] is True
    assert reason == ""
    conn.close()
    _ = live  # 文档：逐标的 live 结果即可，不读 job_runs.stale


def test_daily_cache_ttl_short_in_trading():
    from invest.agent import tools as t

    with mock.patch("invest.intraday._in_trading_window", return_value=True):
        ttl = t._daily_cache_ttl()
    assert 30 <= ttl <= 60
    with mock.patch("invest.intraday._in_trading_window", return_value=False):
        assert t._daily_cache_ttl() >= 300


# ---------- 5) 覆盖率不足时报告降级 ----------

def test_report_degrades_when_coverage_low(monkeypatch):
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.reports import b1_intraday

    calls = {"mood": 0, "mainline": 0}

    def _mood(*a, **k):
        calls["mood"] += 1
        return {"mood": "不应出现", "prediction": "x", "short_term": "y"}

    def _main(*a, **k):
        calls["mainline"] += 1
        return {"main_lines": [{"direction": "不应出现的主线"}]}

    monkeypatch.setattr("invest.skills.sections._intraday_llm.mood_llm", _mood)
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mainline_llm", _main)

    p = _tmp_db("invest_quotes_b1.db")
    conn = connect(p)
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, in_date) "
        "VALUES('600519','core','白酒','2026-08-15')"
    )
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, in_date) "
        "VALUES('000001','core','银行','2026-08-15')"
    )
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, in_date) "
        "VALUES('300750','track','电池','2026-08-15')"
    )
    conn.commit()
    conn.close()

    def _gq(symbols, obj_type="stock", **kw):
        out = []
        for s in symbols:
            ref = parse_asset(s, obj_type)
            assert ref is not None
            if obj_type == "index" and s == "000001":
                out.append(QuoteResult(
                    ref=ref, price=3900.0, pct=0.3, status="live",
                    freshness="live", fallback_level="none", src="tencent",
                ))
            else:
                out.append(QuoteResult(
                    ref=ref, status="missing", fallback_level="source_fail",
                    missing_reason="源失败",
                ))
        return out

    monkeypatch.setattr("invest.data.quotes.get_quotes", _gq)
    monkeypatch.setattr("invest.data.etf.index_etf_signal_text", lambda: "")
    monkeypatch.setattr("invest.data.etf.sector_etf_text", lambda: "")
    import invest.skills.sections.d29_sector_resonance as _d29
    monkeypatch.setattr(_d29, "render", lambda *a, **k: "")
    struct = b1_intraday.render(p)
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert calls["mood"] == 0 and calls["mainline"] == 0
    assert "覆盖率" in texts
    assert "降级" in texts or "事实列表" in texts
    assert "不应出现" not in texts
    assert "不应出现的主线" not in texts
    # 事实列表仍输出
    tables = [s for s in struct["sections"] if s.get("type") == "table"]
    assert tables


def test_degrade_skips_rule_fallback_mainline(monkeypatch):
    """覆盖率不足时禁止「日内主线」完整结论，含规则回退主线；只留事实+告警。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.reports import b1_intraday

    def _mood(*a, **k):
        return {"mood": "不应出现"}

    def _main(*a, **k):
        return {"main_lines": [{"direction": "不应出现的主线"}]}

    monkeypatch.setattr("invest.skills.sections._intraday_llm.mood_llm", _mood)
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mainline_llm", _main)

    p = _tmp_db("invest_quotes_b1_degrade_mainline.db")
    conn = connect(p)
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, in_date) "
        "VALUES('600519','core','白酒','2026-08-15')"
    )
    conn.execute(
        "INSERT INTO industry_bars(industry, date, close, src) "
        "VALUES('半导体','2026-08-26',10.0,'akshare')"
    )
    conn.execute(
        "INSERT INTO industry_bars(industry, date, close, src) "
        "VALUES('半导体','2026-08-27',11.0,'akshare')"
    )
    conn.execute(
        "INSERT INTO sector_fund_flow(date, industry, main_net) "
        "VALUES('2026-08-27','半导体',1500000000)"
    )
    conn.commit()
    conn.close()

    def _gq(symbols, obj_type="stock", **kw):
        out = []
        for s in symbols:
            ref = parse_asset(s, obj_type)
            assert ref is not None
            out.append(QuoteResult(
                ref=ref, status="missing", fallback_level="source_fail",
                missing_reason="源失败",
            ))
        return out

    monkeypatch.setattr("invest.data.quotes.get_quotes", _gq)
    monkeypatch.setattr("invest.data.etf.index_etf_signal_text", lambda: "")
    monkeypatch.setattr("invest.data.etf.sector_etf_text", lambda: "")
    import invest.skills.sections.d29_sector_resonance as _d29
    monkeypatch.setattr(_d29, "render", lambda *a, **k: "")
    struct = b1_intraday.render(p)
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert "告警" in texts
    assert "覆盖率" in texts
    assert "日内主线" not in texts
    assert "不应出现的主线" not in texts
    assert "资金主线" not in texts


def test_b1_core_table_status_column_aligned(monkeypatch):
    """b1 核心表 columns 含「状态」，与 4 列数据对齐。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.reports import b1_intraday

    p = _tmp_db("invest_quotes_b1_status_col.db")
    conn = connect(p)
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, in_date) "
        "VALUES('600519','core','白酒','2026-08-15')"
    )
    conn.commit()
    conn.close()

    def _gq(symbols, obj_type="stock", **kw):
        out = []
        for s in symbols:
            ref = parse_asset(s, obj_type)
            assert ref is not None
            if obj_type == "index":
                out.append(QuoteResult(
                    ref=ref, price=3900.0, pct=0.003, status="live",
                    freshness="unknown", fallback_level="none", src="tencent",
                ))
            else:
                out.append(QuoteResult(
                    ref=ref, price=105.0, pct=0.05, status="live",
                    freshness="live", fallback_level="none", src="sina",
                ))
        return out

    monkeypatch.setattr("invest.data.quotes.get_quotes", _gq)
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mood_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mainline_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.data.etf.index_etf_signal_text", lambda: "")
    monkeypatch.setattr("invest.data.etf.sector_etf_text", lambda: "")
    import invest.skills.sections.d29_sector_resonance as _d29
    monkeypatch.setattr(_d29, "render", lambda *a, **k: "")
    struct = b1_intraday.render(p)
    core = next(s for s in struct["sections"] if s.get("title") == "核心关注实时行情")
    assert "状态" in core["columns"]
    assert all(len(r) == len(core["columns"]) for r in core["rows"])
    assert len(core["columns"]) == 4


def test_b1_index_table_keeps_missing_price(monkeypatch):
    """指数 price is None 也要入表（源失败/缺历史可见）。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.reports import b1_intraday

    p = _tmp_db("invest_quotes_b1_idx_miss.db")

    def _gq(symbols, obj_type="stock", **kw):
        return [
            QuoteResult(
                ref=parse_asset(s, obj_type) or parse_asset("000001", "index"),
                status="missing", fallback_level="source_fail", missing_reason="源失败",
            )
            for s in symbols
        ]

    monkeypatch.setattr("invest.data.quotes.get_quotes", _gq)
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mood_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mainline_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.data.etf.index_etf_signal_text", lambda: "")
    monkeypatch.setattr("invest.data.etf.sector_etf_text", lambda: "")
    import invest.skills.sections.d29_sector_resonance as _d29
    monkeypatch.setattr(_d29, "render", lambda *a, **k: "")
    struct = b1_intraday.render(p)
    ov = next(s for s in struct["sections"] if s.get("title") == "盘面总览")
    assert "状态" in ov["columns"]
    assert len(ov["rows"]) == 8
    assert all(len(r) == 4 for r in ov["rows"])
    assert all(r[1] == "—" for r in ov["rows"])
    assert any("源失败" in r[3] for r in ov["rows"])


def test_a7_key_stocks_and_index_show_status(monkeypatch):
    """a7 关键股票表有状态；指数缺价也入表。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.reports import a7_auction

    p = _tmp_db("invest_quotes_a7_status.db")

    def _gq(symbols, obj_type="stock", **kw):
        out = []
        for s in symbols:
            ref = parse_asset(s, obj_type)
            assert ref is not None
            if obj_type == "index":
                out.append(QuoteResult(
                    ref=ref, status="missing", fallback_level="source_fail",
                    missing_reason="源失败",
                ))
            else:
                out.append(QuoteResult(
                    ref=ref, price=10.0, pct=0.012, status="live",
                    freshness="unknown", fallback_level="none", src="sina",
                ))
        return out

    monkeypatch.setattr("invest.data.quotes.get_quotes", _gq)
    monkeypatch.setattr("invest.data.auction.fetch_top_gainers", lambda limit=10: [])
    monkeypatch.setattr("invest.data.auction.fetch_top_losers", lambda limit=3: [])
    monkeypatch.setattr("invest.data.auction.fetch_vol_top", lambda limit=10: [])
    monkeypatch.setattr(
        "invest.skills.reports.a7_auction._hot_core_stocks",
        lambda conn: [{"block": "半导体", "count": 1,
                       "stocks": [{"symbol": "600001", "name": "某股A", "lianban": 3}]}],
    )
    monkeypatch.setattr("invest.skills.reports.a7_auction._yesterday_ladder", lambda conn: [])
    monkeypatch.setattr("invest.skills.reports.a7_auction._core_symbols", lambda db: [])
    monkeypatch.setattr("invest.skills.sections._intraday_llm.section_analysis_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.auction_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.key_stock_llm", lambda *a, **k: {})

    struct = a7_auction.render(p)
    tables = [s for s in struct["sections"] if s.get("type") == "table"]
    idx = next(t for t in tables if t.get("title") == "指数竞价")
    assert "状态" in idx["columns"]
    assert len(idx["rows"]) == 8
    assert all(len(r) == 4 for r in idx["rows"])
    assert all(r[1] == "—" for r in idx["rows"])

    key = next(t for t in tables if "市场关键股票" in t.get("title", ""))
    assert "状态" in key["columns"]
    assert all(len(r) == len(key["columns"]) for r in key["rows"])
    assert key["rows"][0][-1]


# ---------- 任务 2 Important 质量补强 ----------

def test_fetch_resolved_stale_not_labeled_last_close():
    """fetch_resolved 过期实时对齐 _classify_stock：none + stale，禁止标 last_close。"""
    from invest.data.quotes import status_label
    from invest.data.realtime import RealtimeQuoter

    now = dt.datetime.now()
    stale = Quote(
        symbol="sz000001", price=10.0, pct=0.01,
        ts=now - dt.timedelta(seconds=90), src="sina",
    )

    def _sina(session, symbols):
        return {"sz000001": stale}

    with mock.patch("invest.data.realtime._fetch_sina", side_effect=_sina), \
         mock.patch("invest.data.realtime._fetch_tencent", return_value={}), \
         mock.patch("invest.data.realtime._fetch_em", return_value={}), \
         RealtimeQuoter() as qrt:
        r = qrt.fetch_resolved(["000001"])["000001"]
    assert r.freshness == "stale"
    assert r.fallback_level == "none"
    assert r.fallback_level != "last_close"
    assert status_label(r) == "过期实时"


def test_all_sources_empty_is_source_fail_not_no_history():
    """三源都空 {}（无异常）→ source_fail/源失败，禁止标缺历史。"""
    from invest.data.quotes import get_quotes, status_label
    from invest.data.realtime import RealtimeQuoter

    with mock.patch("invest.data.realtime._fetch_sina", return_value={}), \
         mock.patch("invest.data.realtime._fetch_tencent", return_value={}), \
         mock.patch("invest.data.realtime._fetch_em", return_value={}), \
         RealtimeQuoter() as qrt:
        resolved = qrt.fetch_resolved(["600519"])["600519"]
        via_get = get_quotes(["600519"], obj_type="stock", quoter=qrt)[0]
    assert resolved.fallback_level == "source_fail"
    assert resolved.missing_reason == "源失败"
    assert status_label(resolved) == "源失败"
    assert resolved.fallback_level != "no_history"
    assert via_get.fallback_level == "source_fail"
    assert via_get.missing_reason == "源失败"
    assert status_label(via_get) == "源失败"


def test_degrade_alert_index_zero_live_not_stock_100(monkeypatch):
    """指数全挂时告警写「指数 0 live」，不要打印个股 100%。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.reports import b1_intraday

    p = _tmp_db("invest_quotes_degrade_idx0.db")
    conn = connect(p)
    conn.execute(
        "INSERT INTO candidate_pool(symbol, level, industry, in_date) "
        "VALUES('600519','core','白酒','2026-08-15')"
    )
    conn.commit()
    conn.close()

    def _gq(symbols, obj_type="stock", **kw):
        out = []
        for s in symbols:
            ref = parse_asset(s, obj_type)
            assert ref is not None
            if obj_type == "index":
                out.append(QuoteResult(
                    ref=ref, status="missing", fallback_level="source_fail",
                    missing_reason="源失败",
                ))
            else:
                out.append(QuoteResult(
                    ref=ref, price=105.0, pct=0.05, status="live",
                    freshness="live", fallback_level="none", src="sina",
                ))
        return out

    monkeypatch.setattr("invest.data.quotes.get_quotes", _gq)
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mood_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.mainline_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.data.etf.index_etf_signal_text", lambda: "")
    monkeypatch.setattr("invest.data.etf.sector_etf_text", lambda: "")
    import invest.skills.sections.d29_sector_resonance as _d29
    monkeypatch.setattr(_d29, "render", lambda *a, **k: "")
    struct = b1_intraday.render(p)
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert "指数 0 live" in texts
    assert "100%" not in texts


def test_a7_degrade_skips_auction_mood_rule_fallback(monkeypatch):
    """覆盖不足时禁止「竞价情绪预判」规则回退（对齐 b1 禁止日内主线）。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.reports import a7_auction

    p = _tmp_db("invest_quotes_a7_degrade_mood.db")

    def _gq(symbols, obj_type="stock", **kw):
        return [
            QuoteResult(
                ref=parse_asset(s, obj_type) or parse_asset("000001", obj_type),
                status="missing", fallback_level="source_fail", missing_reason="源失败",
            )
            for s in symbols
        ]

    monkeypatch.setattr("invest.data.quotes.get_quotes", _gq)
    monkeypatch.setattr("invest.data.auction.fetch_top_gainers", lambda limit=10: [])
    monkeypatch.setattr("invest.data.auction.fetch_top_losers", lambda limit=3: [])
    monkeypatch.setattr("invest.data.auction.fetch_vol_top", lambda limit=10: [])
    monkeypatch.setattr("invest.skills.reports.a7_auction._hot_core_stocks", lambda conn: [])
    monkeypatch.setattr("invest.skills.reports.a7_auction._yesterday_ladder", lambda conn: [])
    monkeypatch.setattr("invest.skills.reports.a7_auction._core_symbols", lambda db: [])
    monkeypatch.setattr("invest.skills.sections._intraday_llm.section_analysis_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.auction_llm", lambda *a, **k: {})
    monkeypatch.setattr("invest.skills.sections._intraday_llm.key_stock_llm", lambda *a, **k: {})

    struct = a7_auction.render(p)
    texts = "".join(s.get("text", "") for s in struct["sections"] if s.get("type") == "text")
    assert "竞价情绪预判" not in texts
    assert "规则回退" not in texts


def test_query_realtime_quote_pct_unit_and_etf_fields():
    """指数/ETF pct 同时给出 ratio + 百分数；ETF 保留成交额/量比/主力/超大单。"""
    from invest.agent.tools import query_realtime_quote

    p = _tmp_db("invest_quotes_rt_pct.db")
    conn = connect(p)
    try:
        with mock.patch("invest.data.index_realtime.fetch_index_realtime", return_value={
            "000001": {"name": "上证指数", "price": 3900.0, "pct": 0.35},
        }):
            idx = query_realtime_quote(conn, obj_type="index", symbols=["000001"])
        q = idx["quotes"]["000001"]
        assert q["pct"] == pytest.approx(0.0035)
        assert q["pct_unit"] == "ratio"
        assert q["pct_percent"] == pytest.approx(0.35)

        with mock.patch("invest.data.etf.fetch_etf_quotes", return_value={
            "510300": {
                "name": "沪深300ETF", "price": 4.0, "pct": 0.5,
                "amount": 1.2e9, "vol_ratio": 1.8,
                "main_net": 5e8, "super_net": 3e8,
            },
        }):
            etf = query_realtime_quote(conn, obj_type="etf", symbols=["510300"])
        e = etf["quotes"]["510300"]
        assert e["pct"] == pytest.approx(0.005)
        assert e["pct_unit"] == "ratio"
        assert e["pct_percent"] == pytest.approx(0.5)
        assert e["amount"] == 1.2e9
        assert e["vol_ratio"] == 1.8
        assert e["main_net"] == 5e8
        assert e["super_net"] == 3e8
    finally:
        conn.close()
