"""Agent 推理层单元测试（假 LLM，不打真实 API）。用法: python tests/test_agent.py"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from types import SimpleNamespace

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

if __name__ == "__main__":
    test_tickets_flow()
    test_tools_query()
    test_conflict_detection()
    test_llm_tool_loop()
    test_viewpoint_source_enforced()
    test_query_strength_obj_type()
    test_query_strength_latest_snapshot()
    print("\nALL AGENT TESTS PASSED")