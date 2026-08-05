"""双 Agent：投研 / 交易。只引用工具返回的数据，禁止凭空引用数字。"""
from __future__ import annotations

import sqlite3

from .llm import LLMClient
from .tools import TOOL_SCHEMAS, build_dispatch

RESEARCH_SYSTEM = (
    "你是投研 Agent，回答'钱从哪来、往哪去、哪个方向的基本面在变好'。"
    "可查询宏观流动性、市场温度、候选池；输出观点必须带周期标签与失效条件。"
    "只允许引用工具返回的数字，禁止凭空编造数据。"
)
TRADE_SYSTEM = (
    "你是交易 Agent，回答'市场里的钱现在正在做什么'。"
    "可查询行业强度、板块轮动、资金属性、联动网络、市场温度；"
    "发现强度异常时发起归因请求，输出观点必须带周期标签与失效条件。"
    "只允许引用工具返回的数字，禁止凭空编造数据。"
)


def run_research(conn: sqlite3.Connection, task: str, job: str = "research") -> str:
    client = LLMClient(conn)
    return client.run(RESEARCH_SYSTEM, task, TOOL_SCHEMAS, build_dispatch(conn, source="research"), job=job)


def run_trade(conn: sqlite3.Connection, task: str, job: str = "trade") -> str:
    client = LLMClient(conn)
    return client.run(TRADE_SYSTEM, task, TOOL_SCHEMAS, build_dispatch(conn, source="trade"), job=job)