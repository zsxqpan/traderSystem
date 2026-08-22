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
    """2026-08-18：个股日线工具——本地优先，本地缺失按需联网（东财→新浪回退）。"""
    from invest.agent.tools import _stock_daily_cache, query_stock_daily

    p = _tmp_db()
    conn = connect(p)
    _stock_daily_cache.clear()
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
        r = query_stock_daily(conn, "600519.SH")  # 代码归一化（本地无 → 可能实时联网成功）
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
    test_realtime_health_trading_window_aware()
    test_conflict_detection()
    test_llm_tool_loop()
    test_viewpoint_source_enforced()
    test_query_strength_obj_type()
    test_query_strength_latest_snapshot()
    test_cross_validate()
    test_agent_prompts_include_process()
    print("\nALL AGENT TESTS PASSED")