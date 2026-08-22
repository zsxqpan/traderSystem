"""盘前报告 a0 测试（2026-08-22）：结构化输出 + 删除项 + 双通道渲染。

全部 mock（LLM/网络/数据），不连真实网络。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.data.storage import upsert_df
from invest.db import connect, init_db
from invest.push.render import render_feishu, render_plain
from invest.skills.runner import run_structured


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_premarket_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed(conn):
    upsert_df(conn, "quant_temperature", pd.DataFrame([
        {"run_date": "2026-08-21", "score": 55.0, "profit_effect": 0.55},
    ]))
    upsert_df(conn, "industry_bars", pd.DataFrame([
        {"date": "2026-08-20", "industry": "A", "close": 10.0, "src": "akshare"},
        {"date": "2026-08-21", "industry": "A", "close": 11.0, "src": "akshare"},
        {"date": "2026-08-20", "industry": "B", "close": 20.0, "src": "akshare"},
        {"date": "2026-08-21", "industry": "B", "close": 20.5, "src": "akshare"},
    ]))
    upsert_df(conn, "index_bars", pd.DataFrame([
        {"index_code": "000300", "date": "2026-08-21", "close": 4000.0, "src": "akshare"},
    ]))
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-21", "obj_type": "industry", "obj": "A", "period": "mid",
         "rs": 0.08, "trend_stage": "加速", "calc_version": "v1"},
    ]))
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-21','macro','中性')")
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-21','market','中性')")
    conn.commit()


_DIGEST_OK = {
    "ok": True,
    "risk_items": [
        {"symbol": "600001", "name": "某股", "kind": "业绩雷", "event": "中报预亏", "impact": "短线承压"},
    ],
    "news": {
        "macro": [{"title": "央行降准落地", "impact": "利好流动性"}],
        "stock": [],
        "market_outside": [{"title": "牛来电影票房破圈", "impact": "影视板块关注"}],
    },
    "macro_changed": True,
    "risk_summary": "注意高位股业绩雷风险",
}

_SNAP_ROWS = [
    {"name": "道指", "pct": 0.98},
    {"name": "纳指", "pct": 0.43},
    {"name": "日经225", "pct": -0.3},
    {"name": "韩国KOSPI", "pct": None},  # 数据拿不到 → 该行省略
]


def _render_a0(monkeypatch, agent_focus: str = "半导体：低估值景气回升"):
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    monkeypatch.setattr("invest.skills.sections._digest.overnight_analysis", lambda db: "外围普涨，利好科技成长。")
    monkeypatch.setattr("invest.skills.sections._digest.digest", lambda db: dict(_DIGEST_OK))
    monkeypatch.setattr("invest.data.global_snapshot.global_snapshot_rows", lambda: list(_SNAP_ROWS))
    monkeypatch.setattr("invest.data.halt.fetch_halt_list", list)
    # Agent 关注方向（8:30 落盘）——mock 读取函数，避免污染真实 data/ 目录
    monkeypatch.setattr("invest.skills.reports.a0_premarket._read_agent_focus", lambda: agent_focus)
    return p, run_structured("a0_premarket", db_path=p)


def test_a0_structure(monkeypatch):
    """a0 结构化输出：含外围表格/异动监控表格/消息汇总；不含已删除模块。"""
    _p, struct = _render_a0(monkeypatch)
    sections = struct["sections"]
    texts = "".join(s.get("text", "") for s in sections if s.get("type") == "text")
    tables = [s for s in sections if s.get("type") == "table"]
    # 两个表格：隔夜外围 + 涨停异动监控
    assert len(tables) == 2
    assert tables[0]["title"] == "隔夜外围" and tables[0]["columns"] == ["市场", "涨跌幅"]
    # 韩国无数据 → 不出现 KOSPI 行
    assert all("KOSPI" not in "".join(r) for r in tables[0]["rows"])
    # 异动监控表格含业绩雷行
    assert tables[1]["title"] == "涨停异动监控"
    assert any("业绩雷" in r[0] for r in tables[1]["rows"])
    # 关键节
    assert "外围影响" in texts and "市场温度" in texts and "今日关注" in texts
    assert "消息汇总" in texts and "牛来电影票房破圈" in texts and "央行降准落地" in texts
    # 删除项不存在
    for banned in ("龙虎榜", "板块主线", "指数强弱", "宏观流动性", "依据", "失效条件"):
        assert banned not in texts, f"不应包含删除项: {banned}"


def test_a0_macro_unchanged_hides_macro(monkeypatch):
    """宏观无变化（macro_changed=false）→ 消息汇总不输出宏观组。"""
    d = dict(_DIGEST_OK)
    d["macro_changed"] = False
    monkeypatch.setattr("invest.skills.sections._digest.digest", lambda db: d)
    monkeypatch.setattr("invest.skills.sections._digest.overnight_analysis", lambda db: "x")
    monkeypatch.setattr("invest.data.global_snapshot.global_snapshot_rows", lambda: list(_SNAP_ROWS))
    monkeypatch.setattr("invest.data.halt.fetch_halt_list", list)
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    struct = run_structured("a0_premarket", db_path=p)
    texts = "".join(s.get("text", "") for s in struct["sections"])
    assert "央行降准落地" not in texts  # 宏观变化 false → 不列宏观消息
    assert "牛来电影票房破圈" in texts  # 市场外仍列


def test_render_feishu_card():
    """结构化 → 飞书卡片：schema 2.0、table 组件、**加粗**、*星号*转加粗。"""
    struct = {
        "title": "测试报告",
        "sections": [
            {"type": "text", "text": "**【标题】**\n*强调内容* 普通"},
            {"type": "table", "title": "表", "columns": ["A", "B"], "rows": [["1", "2"]]},
        ],
    }
    card = render_feishu(struct)
    assert card["schema"] == "2.0"
    elements = card["body"]["elements"]
    assert any(e.get("tag") == "table" for e in elements)
    md_text = next(e["text"]["content"] for e in elements if e.get("tag") == "div")
    assert "**【标题】**" in md_text and "**强调内容**" in md_text  # 星号转加粗、**保留
    assert md_text.count("*") % 2 == 0  # 星号成对（无孤立单星号）
    assert card["header"]["title"]["content"] == "测试报告"


def test_render_plain():
    """结构化 → 纯文本：表格转紧凑行、去星号。"""
    struct = {
        "title": "测试",
        "sections": [
            {"type": "text", "text": "**【标题】**\n*强调* 内容"},
            {"type": "table", "title": "隔夜外围", "columns": ["市场", "涨跌幅"],
             "rows": [["道指", "+0.98%"]]},
        ],
    }
    plain = render_plain(struct)
    assert "*" not in plain
    assert "【标题】" in plain and "强调" in plain
    assert "隔夜外围" in plain and "市场 道指" in plain and "+0.98%" in plain
