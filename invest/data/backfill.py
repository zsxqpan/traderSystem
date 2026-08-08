"""历史数据回填：把行情前推到指定日期（默认 2020-01-01）。

- 个股日线（000001 模板）/ 沪深300 指数 / 16 个核心行业（同花顺）
- 龙虎榜/两融/宏观保持原起点（宏观本身全量；龙虎榜从 2024 避免过重）
"""
from __future__ import annotations

from .collector import TASKS, run_collection


def _industry_list() -> list[str]:
    for t in TASKS:
        if t["name"] == "industry_all":
            return list(t["params"].get("industries", []))
    return []


def build_backfill_tasks(
    start_date: str = "20200101",
    end_date: str = "20991231",
) -> list[dict]:
    industries = _industry_list()
    return [
        {
            "name": "daily_bars", "kind": "daily_bars", "table": "daily_bars",
            "sources": ["akshare", "tushare"], "cross_check": True,
            "params": {"symbol": "000001", "start_date": start_date, "end_date": end_date},
        },
        {
            "name": "index_bars", "kind": "index_bars", "table": "index_bars",
            "sources": ["akshare", "tushare"], "cross_check": False,
            "params": {"symbol": "000300", "start_date": start_date, "end_date": end_date},
        },
        {
            "name": "industry_all", "kind": "industry_all", "table": "industry_bars",
            "sources": ["akshare"], "cross_check": False,
            "params": {"start_date": start_date, "end_date": end_date, "industries": industries},
        },
    ]


def run_backfill(
    db_path: str,
    start_date: str = "20200101",
    end_date: str = "20991231",
) -> list[dict]:
    return run_collection(db_path, tasks=build_backfill_tasks(start_date, end_date))


def build_emotion_tasks(days: int = 60) -> list[dict]:
    """最近 days 个工作日（含今天）的情绪任务列表。"""
    import datetime as dt
    tasks = []
    d = dt.date.today()
    while len(tasks) < days:
        if d.weekday() < 5:
            tasks.append({
                "name": f"market_emotion_{d.strftime('%Y%m%d')}",
                "kind": "market_emotion",
                "table": "market_emotion",
                "sources": ["akshare"],
                "cross_check": False,
                "params": {"date": d.strftime("%Y%m%d")},
            })
        d -= dt.timedelta(days=1)
    return tasks


def backfill_emotion(db_path: str, days: int = 60) -> list[dict]:
    """回填最近 days 个交易日的市场情绪数据。"""
    return run_collection(db_path, tasks=build_emotion_tasks(days))


def build_valuation_tasks(years: int = 5) -> list[dict]:
    """近 years 年每个月末工作日的行业估值任务（从当月往回推）。"""
    import datetime as dt
    tasks = []
    today = dt.date.today()
    d = today.replace(day=1)
    for _ in range(years * 12):
        next_month = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        eom = next_month - dt.timedelta(days=1)
        while eom.weekday() >= 5:
            eom -= dt.timedelta(days=1)
        if eom <= today:
            tasks.append({
                "name": f"industry_valuation_{eom.strftime('%Y%m%d')}",
                "kind": "industry_valuation",
                "table": "industry_valuation",
                "sources": ["akshare"],
                "cross_check": False,
                "params": {"date": eom.strftime("%Y%m%d")},
            })
        d = (d.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    return tasks


def backfill_valuation(db_path: str, years: int = 5) -> list[dict]:
    """回填近 years 年行业 PE 历史（每月末）。"""
    return run_collection(db_path, tasks=build_valuation_tasks(years))