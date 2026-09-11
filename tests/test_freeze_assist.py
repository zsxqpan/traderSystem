"""日常表面冻结核对：凯利/工单/BCS 等不得进入报告默认栏目或仪表盘导航。"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]

# 日常报告：允许 d7（观点），禁止把冻结模块当默认栏目
_DAILY_REPORTS = (
    "invest/skills/reports/a0_premarket.py",
    "invest/skills/reports/a3_daily.py",
    "invest/skills/reports/a4_weekly.py",
    "invest/skills/reports/b1_intraday.py",
    "invest/skills/reports/a7_auction.py",
)
_FROZEN_IMPORTS = (
    "invest.discipline.kelly",
    "invest.discipline.clusters",
    "invest.review.bcs",
)
_FROZEN_TOKENS = (
    "kelly",
    "wilson",
    "bcs_score",
    "vms_score",
    "check_cluster_budgets",
    "簇预算",
    "比价主链",
)
_FROZEN_PAGES = (
    "凯利",
    "仓位凯利",
    "工单",
    "BCS",
    "VMS",
    "比价主链",
    "簇预算",
    "联动选方向",
)
_CHAT_KEEP = (
    "query_strength",
    "query_rotation",
    "query_temperature",
    "query_capital",
    "query_linkage",
    "query_macro",
    "query_pool",
    "write_viewpoint",
    "send_direction_hint",
    "request_attribution",
    "query_realtime_health",
    "cross_validate",
    "query_stock_daily",
    "query_realtime_quote",
    "query_lhb",
    "query_data_freshness",
    "web_search",
    "web_fetch",
    "run_skill",
    "run_section",
    "load_skill",
)


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _uses(rel: str) -> list[str]:
    tree = ast.parse(_src(rel))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SKILL":
                    val = ast.literal_eval(node.value)
                    return list(val.get("uses") or [])
    raise AssertionError(f"{rel} 没有 SKILL.uses")


def test_dashboard_pages_only_add_trade_signals():
    from dashboard.nav import PAGES_ORDER

    assert PAGES_ORDER == (
        "市场总览",
        "交易信号",
        "轮动与联动",
        "短线轨",
        "中线轨",
        "中期比价",
        "观点库",
        "大V画像库",
        "执行纪律",
        "回测",
        "数据状态",
    )
    for name in _FROZEN_PAGES:
        assert name not in PAGES_ORDER


def test_daily_reports_do_not_import_frozen_modules():
    for rel in _DAILY_REPORTS:
        text = _src(rel).lower()
        for token in _FROZEN_TOKENS:
            assert token not in text, f"{rel} 含冻结词 {token}"
        tree = ast.parse(_src(rel))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in _FROZEN_IMPORTS, f"{rel} import {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in _FROZEN_IMPORTS, f"{rel} import {alias.name}"


def test_report_uses_no_frozen_columns_besides_d7():
    frozen_ids = {"kelly", "wilson", "bcs", "vms", "cluster", "ticket", "工单"}
    for rel in _DAILY_REPORTS:
        for uid in _uses(rel):
            low = uid.lower()
            assert not any(x in low for x in frozen_ids), f"{rel} uses 含冻结栏目 {uid}"
    assert "d7_agent_viewpoints" in _uses("invest/skills/reports/a4_weekly.py")
    assert "d32_trade_signals" in _uses("invest/skills/reports/a0_premarket.py")
    assert "d32_trade_signals" in _uses("invest/skills/reports/a3_daily.py")
    assert "d32_trade_signals" in _uses("invest/skills/reports/b1_intraday.py")
    assert "d32_trade_signals" in _uses("invest/skills/reports/a7_auction.py")
    assert "d33_daily_actions" in _uses("invest/skills/reports/a3_daily.py")
    assert "d33_daily_actions" in _uses("invest/skills/reports/b1_intraday.py")


def test_chat_tool_schemas_keep_original_tools():
    from invest.agent.tools import TOOL_SCHEMAS

    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    missing = set(_CHAT_KEEP) - names
    assert not missing, f"CHAT 工具缺失 {sorted(missing)}"
    assert "kelly_decision" not in names
    assert "bcs_score" not in names
    assert "vms_score" not in names
