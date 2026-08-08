"""采集编排：任务 → 主备源 → 单源校验 → 落库；失败降级并记录。

设计要点：
- 源按优先级尝试，首个成功即落库；
- 标记 cross_check 的任务会尽力拉取备用源做交叉校验（失败不阻断）；
- 每次调用更新 data_sources 可信度并写 job_runs，永不向上抛异常；
- 全部源的错误都会累积记录在 job_runs.detail，便于排查。
"""
from __future__ import annotations

import time

import pandas as pd

from invest.db import connect

from .sources import SOURCE_REGISTRY
from .storage import upsert_df
from .validator import cross_check, update_credibility

# 采集任务模板（真实运行时可按关注度分级生成参数化任务）
TASKS: list[dict] = [
    {
        "name": "daily_bars",
        "kind": "daily_bars",
        "table": "daily_bars",
        "sources": ["akshare", "tushare"],
        "cross_check": True,
        "params": {"symbol": "000001", "start_date": "20240101", "end_date": "20991231"},
    },
    {
        "name": "index_bars",
        "kind": "index_bars",
        "table": "index_bars",
        "sources": ["akshare", "tushare"],
        "cross_check": False,
        "params": {"symbol": "000300", "start_date": "20240101", "end_date": "20991231"},
    },

    {
        "name": "industry_all",
        "kind": "industry_all",
        "table": "industry_bars",
        "sources": ["akshare"],
        "cross_check": False,
        "params": {
            "start_date": "20240101",
            "end_date": "20991231",
            "industries": [],
        },
    },    {
        "name": "dragon_tiger",
        "kind": "dragon_tiger",
        "table": "dragon_tiger",
        "sources": ["akshare"],
        "cross_check": False,
        "params": {"start_date": "20240101", "end_date": "20991231"},
    },
    {
        "name": "margin",
        "kind": "margin",
        "table": "margin",
        "sources": ["akshare"],
        "cross_check": False,
        "params": {"start_date": "20240101", "end_date": "20240801"},
    },
    {
        "name": "macro_pmi",
        "kind": "macro_series",
        "table": "macro_series",
        "sources": ["akshare"],
        "cross_check": False,
        "params": {"macro": "pmi"},
    },
    {
        "name": "macro_new_financial_credit",
        "kind": "macro_series",
        "table": "macro_series",
        "sources": ["akshare"],
        "cross_check": False,
        "params": {"macro": "new_financial_credit"},
    },
    {
        "name": "industry_valuation",
        "kind": "industry_valuation",
        "table": "industry_valuation",
        "sources": ["akshare"],
        "cross_check": False,
        "params": {},
    },
    {
        "name": "market_emotion",
        "kind": "market_emotion",
        "table": "market_emotion",
        "sources": ["akshare"],
        "cross_check": False,
        "params": {},
    },
    {
        "name": "macro_money_supply",
        "kind": "macro_series",
        "table": "macro_series",
        "sources": ["akshare"],
        "cross_check": False,
        "params": {"macro": "money_supply"},
    },
]

# 各 kind 落库前必须存在的列
_REQUIRED_COLS = {
    "daily_bars": ["date", "symbol", "close"],
    "index_bars": ["date", "index_code", "close"],
    "dragon_tiger": ["date", "symbol"],
    "industry_bars": ["date", "industry", "close"],
    "margin": ["date", "balance"],
    "macro_series": ["indicator", "date", "value"],
}


def run_collection(
    db_path: str,
    tasks: list[dict] | None = None,
    registry: dict | None = None,
    retries: int = 2,
    delay: float = 1.0,
) -> list[dict]:
    """执行一批采集任务，返回逐任务摘要；内部捕获所有异常。"""
    conn = connect(db_path)
    tasks = tasks or TASKS
    registry = registry or SOURCE_REGISTRY
    summary: list[dict] = []
    try:
        for task in tasks:
            summary.append(_run_one(conn, task, registry, retries, delay))
    finally:
        conn.close()
    return summary


def _run_one(
    conn,
    task: dict,
    registry: dict,
    retries: int,
    delay: float,
) -> dict:
    name = task["name"]
    table = task.get("table")
    source_results: list[dict] = []
    errors: list[str] = []
    primary_df: pd.DataFrame | None = None

    for src_name in task["sources"]:
        src = registry.get(src_name)
        if src is None:
            errors.append(f"{src_name}: 未注册")
            continue
        for attempt in range(1, retries + 1):
            try:
                params = dict(task.get("params", {}))
                params["kind"] = task["kind"]
                if task["kind"] in ("market_emotion", "industry_valuation") and "date" not in params:
                    import datetime as _dt
                    params["date"] = _dt.date.today().strftime("%Y%m%d")
                df = src.fetch(params)
                df = src.normalize(df, params)
                if (df is None or df.empty) and task["kind"] == "market_emotion":
                    # 节假日/停市：无涨停池数据，跳过落库（不记 0 涨停、不判失败）
                    update_credibility(conn, src_name, True)
                    source_results.append({"source": src_name, "rows": 0, "written": 0})
                    break
                _check_df(df, task)
                written = upsert_df(conn, table, df) if table else 0
                update_credibility(conn, src_name, True)
                source_results.append({
                    "source": src_name,
                    "rows": int(len(df)),
                    "written": int(written),
                })
                primary_df = df
                break
            except Exception as exc:
                update_credibility(conn, src_name, False)
                errors.append(f"{src_name}(第{attempt}次): {exc}")
                if attempt < retries:
                    time.sleep(delay)
        if source_results:
            break

    status = "ok" if source_results else "failed"
    detail = "; ".join(f"{r['source']}={r['rows']}行" for r in source_results)
    if errors:
        detail = f"{detail} | errors: {'; '.join(errors)}" if detail else "; ".join(errors)

    # 交叉校验（尽力而为，不阻断主流程）
    if (
        status == "ok"
        and task.get("cross_check")
        and len(task["sources"]) > 1
        and primary_df is not None
    ):
        detail = f"{detail} | cross={_try_cross_check(conn, task, registry, primary_df)}"

    _log_job(conn, name, status, detail)
    return {
        "name": name,
        "status": status,
        "sources": source_results,
        "error": "; ".join(errors),
    }


def _check_df(df: pd.DataFrame, task: dict) -> None:
    if df is None or df.empty:
        raise ValueError(f"{task['kind']}: 空数据")
    required = _REQUIRED_COLS.get(task["kind"], [])
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{task['kind']}: 缺少必需列 {missing}")


def _try_cross_check(conn, task: dict, registry: dict, primary_df: pd.DataFrame) -> str:
    """尝试用备用源做关键数据交叉校验；失败返回原因。"""
    backup_name = task["sources"][1]
    src = registry.get(backup_name)
    if src is None:
        return "no-backup"
    try:
        params = dict(task.get("params", {}))
        params["kind"] = task["kind"]
        backup_df = src.fetch(params)
        backup_df = src.normalize(backup_df, params)
        key = "date" if "date" in primary_df.columns and "date" in backup_df.columns else None
        ok, report = cross_check(primary_df, backup_df, key=key)
        update_credibility(conn, backup_name, True)
        return f"ok={ok} rows={report.get('rows')} issues={len(report.get('issues', []))}"
    except Exception as exc:
        update_credibility(conn, backup_name, False)
        return f"cross-failed: {exc}"


def _log_job(conn, job: str, status: str, detail: str) -> None:
    with conn:
        conn.execute(
            """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
               VALUES(?, ?, datetime('now','localtime'), datetime('now','localtime'), ?)""",
            (job, status, detail),
        )