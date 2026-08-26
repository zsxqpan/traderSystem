"""Agent 推理层单元测试（假 LLM，不打真实 API）。用法: python tests/test_agent.py"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.agent import tickets
from invest.agent.arbiter import extract_direction, find_conflicts
from invest.agent.llm import LLMClient
from invest.agent.tools import TOOL_SCHEMAS, build_dispatch
from invest.db import connect, init_db
from invest.viewpoints.store import create_viewpoint, list_viewpoints


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_agent_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_tickets_flow():
    p = _tmp_db()
    conn = connect(p)
    t1 = tickets.create_ticket(conn, "direction_hint", "research", "trade", direction="bull", payload={"obj": "半导体"})
    t2 = tickets.create_ticket(conn, "attribution_request", "trade", "research", payload={"obj": "银行"})
    assert len(tickets.list_tickets(conn)) == 2
    tickets.update_status(conn, t1, "resolved")
    assert tickets.list_tickets(conn, status="resolved")[0]["id"] == t1
    assert tickets.list_tickets(conn, type_="attribution_request")[0]["id"] == t2
    conn.close()
    print("test_tickets_flow OK")


def test_tools_query():
    p = _tmp_db()
    conn = connect(p)
    from invest.data.storage import upsert_df
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-03", "obj_type": "industry", "obj": "半导体", "period": "short",
         "rs": 0.05, "momentum": 0.03, "trend_stage": "启动", "calc_version": "v1"},
        {"run_date": "2026-08-03", "obj_type": "industry", "obj": "银行", "period": "short",
         "rs": 0.02, "momentum": 0.01, "trend_stage": "加速", "calc_version": "v1"},
    ]))
    upsert_df(conn, "quant_temperature", pd.DataFrame([
        {"run_date": "2026-08-03", "limit_up_count": None, "max_lianban": None,
         "zhaban_rate": None, "profit_effect": 0.8, "score": 75.0},
    ]))
    d = build_dispatch(conn)
    top = d["query_strength"](period="short", top=1)
    assert top[0]["obj"] == "半导体"
    temp = d["query_temperature"]()
    assert temp[0]["score"] == 75.0
    conn.close()
    print("test_tools_query OK")


def test_query_stock_daily_db_and_akshare():
    """2026-08-18：个股日线工具——本地优先，本地缺失按需联网（东财→新浪回退）。
    2026-08-25：实时拼当日走 mock（fetch 返回空 → 回落原逻辑），保持全 mock 不联网。"""
    from invest.agent.tools import _stock_daily_cache, query_stock_daily

    p = _tmp_db()
    conn = connect(p)
    _stock_daily_cache.clear()
    _rt_mock = mock.patch("invest.data.realtime.RealtimeQuoter")
    _rt = _rt_mock.start()
    _rt.return_value.__enter__.return_value.fetch.return_value = {}
    try:
        # 本地路径：候选池个股直接出数据
        from invest.data.storage import upsert_df
        upsert_df(conn, "daily_bars", pd.DataFrame([
            {"date": "2026-08-18", "symbol": "300438", "close": 65.55},
            {"date": "2026-08-17", "symbol": "300438", "close": 65.0},
            {"date": "2026-08-14", "symbol": "300438", "close": 64.0},
            {"date": "2026-08-13", "symbol": "300438", "close": 63.5},
            {"date": "2026-08-12", "symbol": "300438", "close": 63.0},
        ]))
        r = query_stock_daily(conn, "600519.SH")  # 代码归一化（本地无 → akshare mock）
        assert r["symbol"] == "600519"
        r2 = query_stock_daily(conn, "300438")
        assert r2["source"] == "db"
        assert r2["latest_close"] == 65.55
        # pct_1d 输出四舍五入到 4 位小数
        assert abs(r2["pct_1d"] - round(65.55 / 65.0 - 1, 4)) < 1e-9

        # akshare 路径：东财失败 → 新浪回退（用未缓存的新代码 000858）
        import pandas as _pd
        sina_df = _pd.DataFrame({
            "date": _pd.to_datetime(["2026-08-19", "2026-08-18", "2026-08-17", "2026-08-14", "2026-08-13"]),
            "close": [1307.88, 1297.99, 1293.09, 1341.99, 1355.29],
        })
        _stock_daily_cache.clear()
        with mock.patch("akshare.stock_zh_a_hist", side_effect=ConnectionError("em down")), mock.patch("akshare.stock_zh_a_daily", return_value=sina_df):
                r3 = query_stock_daily(conn, "000858")
        assert r3["source"] == "akshare"
        assert r3["latest_close"] == 1307.88
        assert r3["latest_date"] == "2026-08-19"
    finally:
        _rt_mock.stop()
        conn.close()
    print("test_query_stock_daily_db_and_akshare OK")


def test_llm_usage_alerts():
    """2026-08-20：用量告警——单次调用超 2 万（1h 限频）+ 当日累计超 50 万（每天一次）。"""
    import pathlib as _pl

    from invest.agent import llm as llm_mod
    from invest.agent.llm import LLMClient

    p = _tmp_db()
    conn = connect(p)
    state_file = os.path.join(tempfile.gettempdir(), "llm_alert_state_test.json")
    pushed = []
    try:
        try:
            os.remove(state_file)
        except OSError:
            pass
        with mock.patch.object(llm_mod, "ALERT_STATE_FILE", _pl.Path(state_file)), \
             mock.patch.object(llm_mod, "_push_alert", lambda title, detail: pushed.append(title)):
            client = LLMClient(conn=conn, settings=type("S", (), {
                "llm_api_key": "sk-test", "llm_base_url": "x", "llm_model": "m"})())
            # 单次超 2 万 → 告警；1 小时内再触发 → 限频不重复
            client._maybe_alert_usage("feishu_chat", 25_000)
            assert len(pushed) == 1 and "单次" in pushed[0]
            client._maybe_alert_usage("feishu_chat", 30_000)
            assert len(pushed) == 1  # 限频
            # 当日累计超 50 万 → 告警（每天一次）
            conn.execute(
                "INSERT INTO llm_usage(date, job, tokens) VALUES(date('now','localtime'), 'research', 500000)"
            )
            conn.commit()
            client._maybe_alert_usage("research", 500)
            assert len(pushed) == 2 and "当日累计" in pushed[1]
            client._maybe_alert_usage("research", 500)
            assert len(pushed) == 2  # 当天不重复
    finally:
        conn.close()
        try:
            os.remove(state_file)
        except OSError:
            pass
    print("test_llm_usage_alerts OK")


def test_chat_system_has_skill_mechanism():
    """2026-08-18：Skill 由大模型语义自选——CHAT_SYSTEM 内置三个 skill 方法论与自标注要求。"""
    from invest.agent.agents import CHAT_SYSTEM

    assert "Skill" in CHAT_SYSTEM
    assert "serenity" in CHAT_SYSTEM and "youzi" in CHAT_SYSTEM and "stock_analysis" in CHAT_SYSTEM
    assert "已使用 Skill" in CHAT_SYSTEM
    print("test_chat_system_has_skill_mechanism OK")


def test_chat_system_has_angle_skills():
    """2026-08-23：CHAT_SYSTEM 内置 9 个角度分析 skill 触发词表（UZI 拆分轻量子 skill）。"""
    from invest.agent.agents import CHAT_SYSTEM

    for name in ("stock-emotion", "stock-technical", "stock-fundamental", "stock-cycle",
                 "trap-scan", "sector-analysis", "opinion-analysis", "big-v-monitor"):
        assert name in CHAT_SYSTEM, f"角度 skill 缺失: {name}"
    # 主角度判别要点（连板→情绪、周期股→周期）已注入
    assert "连板≥2" in CHAT_SYSTEM and "周期行业" in CHAT_SYSTEM
    assert "query_big_v" in CHAT_SYSTEM and "big_v_update" in CHAT_SYSTEM
    print("test_chat_system_has_angle_skills OK")


def test_chat_system_has_realtime_quote_rule():
    """2026-08-26：盘中问个股必须重取实时价——历史价格已过时规则（问题3修复防回归）。"""
    from invest.agent.agents import CHAT_SYSTEM

    assert "历史价格已过时" in CHAT_SYSTEM
    assert "query_realtime_quote" in CHAT_SYSTEM
    assert "不能替代实时报价" in CHAT_SYSTEM
    assert "以实时为准" in CHAT_SYSTEM
    print("test_chat_system_has_realtime_quote_rule OK")


def test_run_skill_uzi_gate():
    """2026-08-23：run_skill 门禁——仅用户明确提到 UZI 时运行；'深度分析'等词拒绝。"""
    from invest.agent.tools import run_skill, set_current_user_text

    try:
        # 未设置用户消息（脚本/测试场景）→ 放行
        set_current_user_text("")
        with mock.patch("invest.agent.skill_runner.run_skill",
                        return_value={"ok": True, "summary": "综合评分 60", "report_path": "r.html"}):
            assert run_skill("600519", depth="lite")["ok"] is True
        # 用户明确提到 UZI → 放行
        set_current_user_text("跑UZI分析600519")
        with mock.patch("invest.agent.skill_runner.run_skill",
                        return_value={"ok": True, "summary": "综合评分 60", "report_path": "r.html"}):
            assert run_skill("600519", depth="lite")["ok"] is True
        # 未提 UZI（"深度分析"）→ 拒绝，不触发流水线
        set_current_user_text("深度分析一下600519")
        out = run_skill("600519", depth="lite")
        assert out["ok"] is False and "UZI" in out["error"]
    finally:
        set_current_user_text("")
    print("test_run_skill_uzi_gate OK")


def test_run_section_tool():
    """2026-08-23：run_section 调报告 D 组小节——日常对话复用现成分析；未知 id 返回 error。"""
    from invest.agent.tools import run_section
    from invest.data.storage import upsert_df

    p = _tmp_db()
    conn = connect(p)
    try:
        upsert_df(conn, "market_emotion", pd.DataFrame([
            {"date": "2026-08-15", "limit_up_count": 45, "max_lianban": 4,
             "zhaban_count": 9, "zhaban_rate": 0.2},
            {"date": "2026-08-14", "limit_up_count": 30, "max_lianban": 3,
             "zhaban_count": 9, "zhaban_rate": 0.3},
        ]))
        upsert_df(conn, "quant_temperature", pd.DataFrame([
            {"run_date": "2026-08-15", "limit_up_count": 45, "max_lianban": 4,
             "zhaban_rate": 0.2, "profit_effect": 0.6, "score": 62.0},
        ]))
        # 有 db_path 的小节（d11 情绪人气）
        out = run_section(conn, "d11_emotion")
        assert out.get("section") == "d11_emotion"
        assert "情绪" in out.get("text", "")
        # 不需要 db_path 的小节（d8 温度倾向只吃 score）
        out2 = run_section(conn, "d8_temp_guide", score=55)
        assert out2.get("text")
        # d1 消息面：mock 电报源 + LLM 失败回退直列素材
        class _FailLLM:
            def __init__(self, *a, **k):
                raise RuntimeError("mock llm down")

        with mock.patch("invest.agent.llm.LLMClient", _FailLLM), mock.patch(
                "invest.report._fetch_telegraph_lines",
                return_value=["2026-08-15 10:00:00 | 央行降准 释放万亿流动性"]):
            out3 = run_section(conn, "d1_news_block")
        assert "降准" in out3.get("text", "")
        # 未知 id → error 不抛异常
        assert "error" in run_section(conn, "no_such_section")
    finally:
        conn.close()
    print("test_run_section_tool OK")


def test_query_realtime_quote():
    """2026-08-25：实时报价工具——stock 三源 / index / etf，实时即最新不受守卫约束。"""
    import datetime as _dt

    from invest.agent.tools import query_realtime_quote
    from invest.data.realtime import Quote

    p = _tmp_db()
    conn = connect(p)
    try:
        q = Quote(symbol="sh600519", price=1304.66, pct=0.025,
                  ts=_dt.datetime(2026, 8, 25, 10, 0), src="sina")
        with mock.patch("invest.data.realtime.RealtimeQuoter") as M:
            M.return_value.__enter__.return_value.fetch.return_value = {"sh600519": q}
            out = query_realtime_quote(conn, symbol="600519")
        assert out["quotes"]["600519"]["price"] == 1304.66
        assert out["quotes"]["600519"]["pct"] == 0.025
        with mock.patch("invest.data.index_realtime.fetch_index_realtime",
                        return_value={"000001": {"name": "上证指数", "price": 3905.2, "pct": 0.35}}):
            out2 = query_realtime_quote(conn, obj_type="index")
        assert out2["quotes"]["000001"]["price"] == 3905.2
        with mock.patch("invest.data.etf.fetch_etf_quotes",
                        return_value={"510300": {"name": "沪深300ETF", "price": 4.0, "pct": 0.5}}):
            out3 = query_realtime_quote(conn, obj_type="etf")
        assert out3["quotes"]["510300"]["price"] == 4.0
        # 无 symbol → error
        assert "error" in query_realtime_quote(conn)
    finally:
        conn.close()
    print("test_query_realtime_quote OK")


def test_query_stock_daily_realtime_patch():
    """2026-08-25：query_stock_daily 本地历史 + 三源实时拼当日（收盘后=收盘价），不等晚间 akshare。"""
    import datetime as dt

    from invest.agent.tools import _stock_daily_cache, query_stock_daily
    from invest.data.realtime import Quote
    from invest.data.storage import upsert_df

    p = _tmp_db()
    conn = connect(p)
    _stock_daily_cache.clear()
    try:
        upsert_df(conn, "daily_bars", pd.DataFrame([
            {"date": "2026-08-21", "symbol": "600519", "close": 1272.83},
            {"date": "2026-08-20", "symbol": "600519", "close": 1291.50},
            {"date": "2026-08-19", "symbol": "600519", "close": 1300.00},
            {"date": "2026-08-18", "symbol": "600519", "close": 1288.00},
        ]))
        q = Quote(symbol="sh600519", price=1304.66, pct=0.025, src="tencent")
        with mock.patch("invest.data.realtime.RealtimeQuoter") as M:
            M.return_value.__enter__.return_value.fetch.return_value = {"sh600519": q}
            out = query_stock_daily(conn, "600519", days=60)
        assert out["source"] == "db+realtime"
        assert abs(out["latest_close"] - 1304.66) < 1e-6
        assert out["latest_date"] == dt.date.today().isoformat()
        assert abs(out["pct_1d"] - round(1304.66 / 1272.83 - 1, 4)) < 1e-9
        # 实时与 akshare 都失败 → error（不硬编）
        _stock_daily_cache.clear()
        with mock.patch("invest.data.realtime.RealtimeQuoter", side_effect=RuntimeError("down")), \
                mock.patch("akshare.stock_zh_a_hist", side_effect=ConnectionError("em down")), \
                mock.patch("akshare.stock_zh_a_daily", side_effect=ConnectionError("sina down")):
            out2 = query_stock_daily(conn, "600519", days=60)
        assert "error" in out2
    finally:
        conn.close()
    print("test_query_stock_daily_realtime_patch OK")


def test_query_lhb():
    """2026-08-25：龙虎榜个股查询——本地 dragon_tiger 按股票/名称查；无参数报错。"""
    from invest.agent.tools import query_lhb
    from invest.data.storage import upsert_df

    p = _tmp_db()
    conn = connect(p)
    try:
        upsert_df(conn, "dragon_tiger", pd.DataFrame([
            {"date": "2026-07-27", "symbol": "002083", "name": "孚日股份", "seat_type": "list",
             "buy": 3.26e8, "sell": 4.50e8, "net": -1.24e8},
            {"date": "2026-07-24", "symbol": "002083", "name": "孚日股份", "seat_type": "list",
             "buy": 2.96e8, "sell": 1.32e8, "net": 1.64e8},
            {"date": "2026-07-27", "symbol": "002083", "name": "孚日股份", "seat_type": None,
             "buy": 3.26e8, "sell": 4.50e8, "net": -1.24e8},  # 历史重复行（seat_type NULL）应被过滤
            {"date": "2026-07-24", "symbol": "600519", "name": "贵州茅台", "seat_type": "list",
             "buy": 1e8, "sell": 2e8, "net": -1e8},
        ]))
        r = query_lhb(conn, symbol="002083", n=5)
        assert r["rows"] and len(r["rows"]) == 2  # 过滤 seat_type NULL 重复
        assert r["rows"][0]["date"] == "2026-07-27"  # 倒序
        assert abs(float(r["rows"][0]["net"]) + 1.24e8) < 1
        # 按名称查
        r2 = query_lhb(conn, name="孚日")
        assert len(r2["rows"]) == 2
        # 无参数 → error
        assert "error" in query_lhb(conn)
        # 无数据 → 空 rows 不报错
        r3 = query_lhb(conn, symbol="999999")
        assert r3["rows"] == []
    finally:
        conn.close()
    print("test_query_lhb OK")


def test_xueqiu_fetch_tools():
    """2026-08-25：雪球 Playwright 工具——fetch_user 建画像、fetch_article 抓正文入库去重。"""
    from invest.agent.tools import query_big_v, xueqiu_fetch_article, xueqiu_fetch_user

    p = _tmp_db()
    conn = connect(p)
    try:
        # fetch_user：mock 主页动态 → 建 profile + 返回列表
        with mock.patch("invest.data.xueqiu_fetch.fetch_user_statuses",
                        return_value=[{"url": "https://xueqiu.com/6192813830/366009201",
                                       "title": "段永平重出江湖", "time": "", "snippet": ""}]):
            r = xueqiu_fetch_user(conn, "6192813830")
        assert r["ok"] is True and r["profile_id"] == "xq_6192813830"
        prof = query_big_v(conn, profile_id="xq_6192813830")
        assert len(prof["profiles"]) == 1
        assert "xueqiu.com/u/6192813830" in prof["profiles"][0]["homepage"]
        # fetch_article：mock 正文 → 入库（按 url 去重）
        with mock.patch("invest.data.xueqiu_fetch.fetch_article", return_value={
                "url": "https://xueqiu.com/6192813830/366009201",
                "title": "段永平重出江湖", "time": "2025-12-15 10:46",
                "author": "期货兵法", "text": "退休二十多年的段永平为何频频出圈…"}):
            r2 = xueqiu_fetch_article(conn, "https://xueqiu.com/6192813830/366009201",
                                      profile_id="xq_6192813830")
        assert r2["ok"] is True and "段永平" in r2["title"]
        op = query_big_v(conn, profile_id="xq_6192813830")["opinions"]
        assert len(op) == 1 and op[0]["url"].endswith("366009201")
        # 去重：同 url 二次调用不重复入库
        with mock.patch("invest.data.xueqiu_fetch.fetch_article", return_value={
                "url": "https://xueqiu.com/6192813830/366009201", "title": "x",
                "time": "", "author": "", "text": "y"}):
            xueqiu_fetch_article(conn, "https://xueqiu.com/6192813830/366009201",
                                 profile_id="xq_6192813830")
        assert len(query_big_v(conn, profile_id="xq_6192813830")["opinions"]) == 1
        # profile 不存在 → error
        with mock.patch("invest.data.xueqiu_fetch.fetch_article", return_value={
                "url": "u", "title": "t", "time": "", "author": "", "text": "x"}):
            r3 = xueqiu_fetch_article(conn, "u", profile_id="xq_nobody")
        assert "error" in r3
        # 抓取失败 → error
        with mock.patch("invest.data.xueqiu_fetch.fetch_article", return_value=None):
            r4 = xueqiu_fetch_article(conn, "u")
        assert "error" in r4
    finally:
        conn.close()
    print("test_xueqiu_fetch_tools OK")


def test_run_section_covers_all_sections():
    """2026-08-23：run_section 工具描述覆盖全部注册 D 组小节，防新增小节漏列。"""
    import invest.skills  # noqa: F401  触发注册
    from invest.agent.tools import TOOL_SCHEMAS
    from invest.skills import registry

    desc = next(t["function"]["description"] for t in TOOL_SCHEMAS
                if t["function"]["name"] == "run_section")
    missing = [sid for sid in registry.list_skills("section") if sid not in desc]
    assert not missing, f"run_section 描述漏了小节: {missing}"
    print("test_run_section_covers_all_sections OK")


def test_freshness_gate():
    """2026-08-23：对话守卫——数据滞后时数据工具返回原因而非旧数据；数据新/守卫关时正常。"""
    import datetime as dt

    from invest.agent.tools import _fresh_cache, _stock_daily_cache, build_dispatch, freshness_gate, set_freshness_gate
    from invest.data.calendar import latest_trading_day
    from invest.data.storage import upsert_df

    p = _tmp_db()
    conn = connect(p)
    _stock_daily_cache.clear()
    try:
        exp = latest_trading_day(dt.date.today()).isoformat()
        # 旧数据（远早于最近交易日）
        upsert_df(conn, "daily_bars", pd.DataFrame([
            {"date": "2026-01-05", "symbol": "600519", "close": 100.0},
        ]))
        upsert_df(conn, "index_bars", pd.DataFrame([
            {"index_code": "000300", "date": "2026-01-05", "close": 4000.0},
        ]))
        _fresh_cache.clear()
        with mock.patch("invest.intraday._in_trading_window", return_value=False):
            ok, reason = freshness_gate(conn)
        assert ok is False and "数据截至" in reason
        # 守卫开启：个股行情工具（query_stock_daily）返回 error 而非旧数据；
        # query_temperature（涨停池独立采集）不受 daily_bars 守卫约束（2026-08-25 收窄）
        d = build_dispatch(conn)
        try:
            set_freshness_gate(True)
            with mock.patch("invest.intraday._in_trading_window", return_value=False):
                out = d["query_temperature"]()
                r2 = d["query_stock_daily"](symbol="600519")
        finally:
            set_freshness_gate(False)
        assert not (isinstance(out, dict) and "数据截至" in out.get("error", ""))
        assert "error" in r2 and "数据截至" in r2["error"]
        # 2026-08-25：run_section 分级守卫——消息汇总(d27)/情绪(d11)不依赖 daily_bars 可正常用；
        # 直接读 daily_bars 的小节（d18 异常波动）仍被拦截
        from invest.agent.tools import run_section

        try:
            set_freshness_gate(True)
            with mock.patch("invest.skills.sections._digest.digest", return_value={
                    "ok": True, "macro_changed": True,
                    "news": {"macro": [{"title": "央行降准", "impact": "释放流动性"}]}}):
                out_msg = run_section(conn, "d27_news_digest")
            assert "error" not in out_msg and "央行降准" in out_msg.get("text", "")
            with mock.patch("invest.intraday._in_trading_window", return_value=False):
                out_sect = run_section(conn, "d18_abnormal_moves")
            assert "error" in out_sect and "数据截至" in out_sect["error"]
        finally:
            set_freshness_gate(False)
        # 2026-08-24 盘前放宽：日线或指数**任一**到最近交易日即可放行（如 8-25 盘前用 8-24 数据）
        _fresh_cache.clear()
        upsert_df(conn, "daily_bars", pd.DataFrame([
            {"date": exp, "symbol": "600519", "close": 100.0},
        ]))
        with mock.patch("invest.intraday._in_trading_window", return_value=False):
            ok2, _ = freshness_gate(conn)
        assert ok2 is True  # daily 到最近交易日（index 仍旧）→ 放行
        # 数据到最近交易日（≥5 行满足本地路径）→ 放行
        _fresh_cache.clear()
        _stock_daily_cache.clear()
        rows = [{"date": (dt.date.fromisoformat(exp) - dt.timedelta(days=i)).isoformat(),
                 "symbol": "600519", "close": 100.0} for i in range(6)]
        upsert_df(conn, "daily_bars", pd.DataFrame(rows))
        upsert_df(conn, "index_bars", pd.DataFrame([
            {"index_code": "000300", "date": exp, "close": 4000.0},
        ]))
        try:
            set_freshness_gate(True)
            with mock.patch("invest.intraday._in_trading_window", return_value=False), \
                    mock.patch("invest.data.realtime.RealtimeQuoter") as _M:
                _M.return_value.__enter__.return_value.fetch.return_value = {}
                out2 = d["query_stock_daily"](symbol="600519")
        finally:
            set_freshness_gate(False)
        assert out2["latest_close"] == 100.0
        # 守卫关闭（默认）：旧数据也放行（脚本/测试兼容）
        _fresh_cache.clear()
        out3 = d["query_stock_daily"](symbol="600519")
        assert out3["latest_close"] == 100.0
        # 2026-08-24：quant 衍生指标滞后不阻塞——daily/index 新 + quant 旧 → fresh=True 且 quant_stale=True
        upsert_df(conn, "quant_strength", pd.DataFrame([
            {"run_date": "2026-01-05", "obj_type": "industry", "obj": "A", "period": "short",
             "rs": 0.1, "calc_version": "v1"},
        ]))
        from invest.agent.tools import query_data_freshness

        f = query_data_freshness(conn)
        assert f["fresh"] is True and f.get("quant_stale") is True
        assert "quant" not in (f.get("stale_parts") or [])
    finally:
        conn.close()
    print("test_freshness_gate OK")


def test_finance_keyword_detection():
    """2026-08-27：金融词判别表——补财报类词（半年报/业绩/环比等），非金融词不误判。"""
    from invest.agent.agents import _is_finance

    # 非金融（常识/人名/闲聊）
    assert _is_finance("五女一是谁") is False
    assert _is_finance("今天天气怎么样") is False
    assert _is_finance("帮我解释一下什么是复利") is False
    assert _is_finance("周末去爬山怎么样") is False
    # 金融
    assert _is_finance("600519 现在能买吗") is True
    assert _is_finance("半导体板块今天怎么样") is True
    assert _is_finance("贵州茅台的基本面怎么样") is True
    assert _is_finance("今天大盘什么情况") is True
    assert _is_finance("这个票 PE 太高了吧") is True
    # 2026-08-27 补词：财报/业绩类问题不再漏判（此前走 GENERAL 降级导致编造数据）
    assert _is_finance("分析华工科技半年报") is True
    assert _is_finance("重要的是他的季度环比数据") is True
    assert _is_finance("华工科技扣非净利润如何") is True
    assert _is_finance("看看宁德时代的业绩预告") is True
    print("test_finance_keyword_detection OK")


def test_run_chat_full_capability():
    """2026-08-27：全能 Agent——run_chat 一律走 CHAT_SYSTEM（不分流降级）、max_turns=6、全工具含 load_skill。"""
    import invest.agent.agents as agents_mod
    from invest.agent.agents import CHAT_SYSTEM, run_chat

    p = _tmp_db()
    conn = connect(p)
    try:
        calls: dict = {}

        class _FakeLLM:
            def __init__(self, *a, **k):
                pass

            def run(self, system, user, tools, dispatch, job, max_turns, history=None):
                calls["system"] = system
                calls["max_turns"] = max_turns
                calls["tools"] = tools
                return "ok"

        with mock.patch.object(agents_mod, "LLMClient", _FakeLLM):
            # 金融问题与非金融问题都走同一全能提示词（不再降级）
            run_chat(conn, "分析华工科技半年报", job="test", chat_id="")
            assert calls["system"] == CHAT_SYSTEM
            run_chat(conn, "帮我 grill 一下我的交易系统设计", job="test", chat_id="")
            assert calls["system"] == CHAT_SYSTEM
        assert calls["max_turns"] == 6
        assert any(t["function"]["name"] == "load_skill" for t in calls["tools"])
    finally:
        conn.close()
    print("test_run_chat_full_capability OK")


def test_load_skill_tool():
    """2026-08-27：load_skill——读 SKILL.md 全文、别名归一、未知名返回可用清单。"""
    from invest.agent.tools import TOOL_SCHEMAS, build_dispatch, load_skill

    # 正常加载（真实文件）：grill-me 是壳，指向 grilling 方法论
    r = load_skill("grill-me")
    assert r.get("skill") == "grill-me" and "grilling" in r.get("text", "")
    r_full = load_skill("grilling")
    assert r_full.get("skill") == "grilling" and len(r_full.get("text", "")) > 300
    # 别名：debug → systemdebugging（完整方法论）
    r2 = load_skill("debug")
    assert r2.get("skill") == "systemdebugging" and len(r2.get("text", "")) > 300
    # 未知名 → error + 可用清单
    r3 = load_skill("no_such_skill_xyz")
    assert "error" in r3 and r3.get("available")
    # 已注册进 schema 与 dispatch（不绑 conn，可无 db 调用）
    names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert "load_skill" in names
    d = build_dispatch(None)
    r4 = d["load_skill"](name="brainstorming")
    assert r4.get("skill") == "brainstorming"
    print("test_load_skill_tool OK")


def test_big_v_tables_created():
    """2026-08-23：init_db 建出 big_v_profile / big_v_opinion 两表；2026-08-24：chat_history 表。"""
    from invest.db import table_names

    p = _tmp_db()
    names = table_names(p)
    assert "big_v_profile" in names and "big_v_opinion" in names
    assert "chat_history" in names
    print("test_big_v_tables_created OK")


def test_chat_memory_history():
    """2026-08-24：对话记忆——run_chat 带 chat_id 读历史注入 + 写回 chat_history；无 chat_id 无状态。"""
    from invest.agent.agents import _load_history, run_chat

    p = _tmp_db()
    conn = connect(p)
    try:
        calls: dict = {}

        class _FakeLLM:
            def __init__(self, *a, **k):
                calls["client"] = self

            def run(self, system, user, tools, dispatch, job, max_turns, history=None):
                calls["history"] = list(history or [])
                calls["user"] = user
                return "第一轮回复"

        # 第一轮：无历史
        with mock.patch("invest.agent.agents.LLMClient", _FakeLLM):
            out1 = run_chat(conn, "600519怎么样", job="test", chat_id="oc_mem1")
        assert out1 == "第一轮回复" and calls["history"] == []
        # 第二轮：历史包含上一轮 user + assistant（上下文记忆）
        with mock.patch("invest.agent.agents.LLMClient", _FakeLLM):
            run_chat(conn, "那支撑位在哪", job="test", chat_id="oc_mem1")
        assert calls["history"] == [
            {"role": "user", "content": "600519怎么样"},
            {"role": "assistant", "content": "第一轮回复"},
        ]
        # 无 chat_id → 无状态（不注入也不写入）
        with mock.patch("invest.agent.agents.LLMClient", _FakeLLM):
            run_chat(conn, "测试", job="test")
        assert calls["history"] == []
        assert _load_history(conn, "") == []
        # 持久化：新连接也能读到历史
        conn2 = connect(p)
        try:
            hist = _load_history(conn2, "oc_mem1")
            assert hist[0]["role"] == "user" and hist[-1]["role"] == "assistant"
        finally:
            conn2.close()
    finally:
        conn.close()
    print("test_chat_memory_history OK")


def test_big_v_tools():
    """2026-08-23：big_v_update / query_big_v 读写闭环 + 错误路径（全 mock 临时库）。"""
    from invest.agent.tools import big_v_update, query_big_v

    p = _tmp_db()
    conn = connect(p)
    try:
        # 错误路径：无 name
        r = big_v_update(conn, action="upsert_profile")
        assert r["ok"] is False and "name" in r["error"]
        # 错误路径：opinion 引用的画像不存在
        r = big_v_update(conn, action="upsert_opinion", profile_id="xq_nobody", view="看多")
        assert r["ok"] is False and "先 upsert_profile" in r["error"]
        # 建画像（profile_id 缺省自动生成）
        r = big_v_update(conn, action="upsert_profile", name="段永平", style="价投",
                         strengths="消费/互联网", win_rate="自称长期赢家")
        assert r["ok"] is True and r["profile_id"] == "xq_段永平"
        # 加观点
        r = big_v_update(conn, action="upsert_opinion", profile_id="xq_段永平",
                         opinion_date="2026-08-20", symbol="600519", view="商业模式好", bias="bullish")
        assert r["ok"] is True and r["opinion_id"] is not None
        # 更新画像（同 id 覆盖）
        r = big_v_update(conn, action="upsert_profile", profile_id="xq_段永平", name="段永平",
                         notes="补充：重仓苹果/茅台/腾讯")
        assert r["ok"] is True
        # 查询：精确 id
        out = query_big_v(conn, profile_id="xq_段永平")
        assert len(out["profiles"]) == 1 and out["profiles"][0]["notes"].startswith("补充")
        assert len(out["opinions"]) == 1 and out["opinions"][0]["bias"] == "bullish"
        # 查询：模糊 name
        out2 = query_big_v(conn, name="段永平")
        assert len(out2["profiles"]) == 1
        # 查询：空条件列出最近画像
        out3 = query_big_v(conn)
        assert len(out3["profiles"]) == 1
    finally:
        conn.close()
    print("test_big_v_tools OK")


def test_realtime_health_trading_window_aware():
    """2026-08-18：非交易时段休市，实时行情旧属正常——query_realtime_health 返回 ok=True
    并提示用日线数据，不再误报"数据失效"（避免盘后艾特 Agent 分析个股被拒）。"""
    from invest.agent.tools import query_realtime_health

    # 非交易时段 → ok=True + 休市提示（函数内局部导入，patch 源模块）
    with mock.patch("invest.intraday._in_trading_window", return_value=False):
        out = query_realtime_health(None)
    assert out["ok"] is True
    assert "非交易时段" in out["note"]

    # 交易时段 → 走真实 realtime_health（mock 其返回）
    with mock.patch("invest.intraday._in_trading_window", return_value=True), mock.patch(
            "invest.data.realtime.realtime_health",
            return_value={"ok": False, "stale": 2, "last_detail": "stale=2"}):
        out2 = query_realtime_health(None)
    assert out2["ok"] is False and out2["stale"] == 2
    print("test_realtime_health_trading_window_aware OK")


def test_conflict_detection():
    p = _tmp_db()
    conn = connect(p)
    common = {
        "source": "research", "obj_type": "industry", "obj": "半导体", "period_tag": "mid",
        "confidence": 0.7, "evidence": [{"x": 1}], "invalid_condition": "RS转负",
    }
    v1 = create_viewpoint(conn, **{**common, "conclusion": "半导体中期看多，向上趋势延续"})
    v2 = create_viewpoint(conn, **{**common, "source": "trade", "conclusion": "半导体中期看空，向下概率大"})
    assert extract_direction("看多") == "bull"
    assert extract_direction("看空") == "bear"
    pairs = find_conflicts(conn)
    assert (v1, v2) in pairs or (v2, v1) in pairs
    conn.close()
    print("test_conflict_detection OK")


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=arguments)

    def model_dump(self):
        return {"id": self.id, "function": {"name": self.function.name, "arguments": self.function.arguments}}


class _FakeMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        msg = self._responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg)],
            usage=SimpleNamespace(total_tokens=10),
        )


class _FakeOpenAI:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


def test_llm_tool_loop():
    p = _tmp_db()
    conn = connect(p)
    llm = LLMClient(conn)
    call = json.dumps({
        "source": "research", "conclusion": "测试观点", "period_tag": "short",
        "confidence": 0.6, "evidence": [{"tool": "query_temperature"}],
        "invalid_condition": "测试失效", "obj": "半导体",
    }, ensure_ascii=False)
    llm.client = _FakeOpenAI([
        _FakeMessage(None, [_FakeToolCall("call_1", "write_viewpoint", call)]),
        _FakeMessage("已生成观点"),
    ])
    out = llm.run("sys", "user", TOOL_SCHEMAS, build_dispatch(conn), job="test")
    assert out == "已生成观点"
    vps = list_viewpoints(conn, source="research")
    assert len(vps) == 1 and vps[0]["obj"] == "半导体"
    conn.close()
    print("test_llm_tool_loop OK")




def test_viewpoint_source_enforced():
    """回归：来源必须白名单；工具层注入固定来源，LLM 传 source 无效。"""
    p = _tmp_db()
    conn = connect(p)
    try:
        create_viewpoint(
            conn, source="hacker", conclusion="x", period_tag="short",
            confidence=0.5, evidence=[{"a": 1}], invalid_condition="y",
        )
        raise AssertionError("should reject invalid source")
    except ValueError:
        pass
    d = build_dispatch(conn, source="trade")
    d["write_viewpoint"](
        source="research",  # 旧/恶意参数：应被服务端覆盖
        conclusion="测试观点", period_tag="short", confidence=0.6,
        evidence=[{"tool": "x"}], invalid_condition="失效", obj="600519",
    )
    rows = list_viewpoints(conn, source="trade")
    assert len(rows) == 1 and rows[0]["obj"] == "600519"
    conn.close()
    print("test_viewpoint_source_enforced OK")



def test_query_strength_obj_type():
    """回归：强度榜按 obj_type 过滤，行业与个股不混排。"""
    p = _tmp_db()
    conn = connect(p)
    from invest.data.storage import upsert_df
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-03", "obj_type": "industry", "obj": "半导体", "period": "short",
         "rs": 0.05, "momentum": 0.03, "trend_stage": "启动", "calc_version": "v1"},
        {"run_date": "2026-08-03", "obj_type": "stock", "obj": "000001", "period": "short",
         "rs": 0.99, "momentum": 0.5, "trend_stage": "加速", "calc_version": "v1"},
    ]))
    d = build_dispatch(conn)
    assert d["query_strength"](top=10)[0]["obj"] == "半导体"
    assert d["query_strength"](obj_type="stock")[0]["obj"] == "000001"
    conn.close()
    print("test_query_strength_obj_type OK")



def test_query_strength_latest_snapshot():
    """回归：强度榜只返回最新 run_date 快照，不混入历史数据。"""
    p = _tmp_db()
    conn = connect(p)
    from invest.data.storage import upsert_df
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-03", "obj_type": "industry", "obj": "白酒", "period": "short",
         "rs": 0.99, "momentum": 0.5, "trend_stage": "加速", "calc_version": "v1"},
        {"run_date": "2026-08-04", "obj_type": "industry", "obj": "白酒", "period": "short",
         "rs": 0.12, "momentum": 0.1, "trend_stage": "启动", "calc_version": "v1"},
        {"run_date": "2026-08-04", "obj_type": "industry", "obj": "教育", "period": "short",
         "rs": 0.20, "momentum": 0.15, "trend_stage": "启动", "calc_version": "v1"},
    ]))
    d = build_dispatch(conn)
    top = d["query_strength"](top=5)
    assert [r["obj"] for r in top] == ["教育", "白酒"]
    assert abs(top[0]["rs"] - 0.20) < 1e-9 and abs(top[1]["rs"] - 0.12) < 1e-9
    conn.close()
    print("test_query_strength_latest_snapshot OK")


def test_cross_validate():
    """多源交叉验证（A-Stock-Skills 多源校验思想）：四维度汇总。"""
    p = _tmp_db()
    conn = connect(p)
    from invest.data.storage import upsert_df
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-03", "obj_type": "industry", "obj": "半导体", "period": "short",
         "rs": 0.15, "rs5": 0.05, "rs10": 0.1, "rs20": 0.2, "momentum": 0.1,
         "trend_stage": "加速", "calc_version": "v1"},
    ]))
    upsert_df(conn, "quant_capital", pd.DataFrame([
        {"run_date": "2026-08-03", "obj_type": "industry", "obj": "半导体",
         "fund_type": "游资", "style": "主题炒作", "confidence": 0.8},
    ]))
    upsert_df(conn, "quant_linkage", pd.DataFrame([
        {"run_date": "2026-08-03", "a": "半导体", "b": "元件", "corr": 0.85, "lead": "半导体"},
    ]))
    upsert_df(conn, "quant_valuation", pd.DataFrame([
        {"run_date": "2026-08-03", "obj": "半导体", "pe_pct": 0.35, "crowding": 0.7,
         "crowding_state": "拥挤"},
    ]))
    d = build_dispatch(conn)
    r = d["cross_validate"]("半导体", obj_type="industry")
    assert r["obj"] == "半导体"
    assert r["dimensions"]["strength"]["trend_stage"] == "加速"
    assert r["dimensions"]["capital"]["style"] == "主题炒作"
    assert r["dimensions"]["linkage"][0]["corr"] >= 0.7
    assert r["dimensions"]["valuation"]["pe_pct"] == 0.35
    # 个股交叉验证（无行业映射时至少返回 strength/capital）
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-03", "obj_type": "stock", "obj": "600519", "period": "short",
         "rs": 0.1, "momentum": 0.05, "trend_stage": "启动", "calc_version": "v1"},
    ]))
    upsert_df(conn, "quant_capital", pd.DataFrame([
        {"run_date": "2026-08-03", "obj_type": "stock", "obj": "600519",
         "fund_type": None, "style": "震荡", "confidence": 0.3},
    ]))
    r2 = d["cross_validate"]("600519", obj_type="stock")
    assert r2["dimensions"]["strength"]["obj"] == "600519"
    # 行业维度在 industry 子键（不覆盖个股维度）
    assert r2["dimensions"]["industry"]["name"] == "白酒"
    conn.close()
    print("test_cross_validate OK")


def test_agent_prompts_include_process():
    """回归：A-Stock-Skills 分析流程已融入 system prompt。"""
    from invest.agent.agents import RESEARCH_SYSTEM, TRADE_SYSTEM
    for sys_prompt in (RESEARCH_SYSTEM, TRADE_SYSTEM):
        assert "分析流程" in sys_prompt
        assert "交叉验证" in sys_prompt
        assert "失效条件" in sys_prompt
        assert "数据失效即防守" in sys_prompt
        assert "trade-journal" in sys_prompt or "问责" in sys_prompt
    print("test_agent_prompts_include_process OK")


if __name__ == "__main__":
    test_tickets_flow()
    test_tools_query()
    test_query_stock_daily_db_and_akshare()
    test_llm_usage_alerts()
    test_chat_system_has_skill_mechanism()
    test_chat_system_has_angle_skills()
    test_finance_keyword_detection()
    test_run_chat_full_capability()
    test_load_skill_tool()
    test_run_skill_uzi_gate()
    test_run_section_tool()
    test_run_section_covers_all_sections()
    test_query_realtime_quote()
    test_query_stock_daily_realtime_patch()
    test_query_lhb()
    test_xueqiu_fetch_tools()
    test_freshness_gate()
    test_big_v_tables_created()
    test_chat_memory_history()
    test_big_v_tools()
    test_realtime_health_trading_window_aware()
    test_conflict_detection()
    test_llm_tool_loop()
    test_viewpoint_source_enforced()
    test_query_strength_obj_type()
    test_query_strength_latest_snapshot()
    test_cross_validate()
    test_agent_prompts_include_process()
    print("\nALL AGENT TESTS PASSED")