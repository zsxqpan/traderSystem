"""运行回测：趋势阶段 / 风格 / 风格×温度 / 评级-仓位映射校准。

用法: myenv\\Scripts\\python.exe scripts/run_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from backtest.rules.rating_map import metrics_json as rm_metrics
from backtest.rules.rating_map import run_rating_map_backtest
from backtest.rules.style import metrics_json as style_metrics
from backtest.rules.style import run_style_backtest
from backtest.rules.style_temp import metrics_json as st_metrics
from backtest.rules.style_temp import run_style_temp_backtest
from backtest.rules.trend_stage import metrics_json as ts_metrics
from backtest.rules.trend_stage import run_trend_stage_backtest
from invest.db import connect, init_db


def _load(db_path: str):
    conn = connect(db_path)
    try:
        ind = pd.read_sql_query(
            "SELECT date, industry, close, amount FROM industry_bars ORDER BY date", conn,
        )
        idx = pd.read_sql_query(
            "SELECT date, close FROM index_bars WHERE index_code='000300' ORDER BY date", conn,
        )
        macro = pd.read_sql_query(
            "SELECT date, indicator, value FROM macro_series ORDER BY date", conn,
        )
    finally:
        conn.close()
    ind["date"] = pd.to_datetime(ind["date"], format="mixed", errors="coerce")
    idx["date"] = pd.to_datetime(idx["date"], format="mixed", errors="coerce")
    closes = ind.pivot_table(index="date", columns="industry", values="close")
    amounts = ind.pivot_table(index="date", columns="industry", values="amount")
    returns = closes.pct_change().replace([float("inf"), float("-inf")], float("nan"))
    benchmark = idx.set_index("date")["close"]
    return closes, amounts, returns, benchmark, macro


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "invest.db")
    init_db(db_path)
    closes, amounts, returns, benchmark, macro = _load(db_path)

    ts_ex = run_trend_stage_backtest(closes, benchmark=benchmark)
    style_ex = run_style_backtest(closes, returns, benchmark=benchmark)
    style_temp_ex = run_style_temp_backtest(closes, returns, amounts, benchmark)
    rating_map = run_rating_map_backtest(benchmark, macro)

    print("=== 趋势阶段 · 超额收益 ===")
    if not ts_ex.empty:
        print(ts_ex.to_string(index=False))
    print("\n=== 风格标签 · 超额收益 ===")
    if not style_ex.empty:
        print(style_ex.to_string(index=False))
    print("\n=== 风格 × 温度 · 超额收益 ===")
    if not style_temp_ex.empty:
        print(style_temp_ex.to_string(index=False))
    print("\n=== 评级-仓位映射校准 ===")
    if not rating_map["stats"].empty:
        print(rating_map["stats"].to_string(index=False))
        print("建议映射（fwd20 线性 0.05-0.80）:")
        for k, v in rating_map["suggestion"].items():
            print(f"  {k} = {v:.0%}")

    conn = connect(db_path)
    try:
        from invest.data.storage import upsert_df
        daterange = f"{closes.index.min().date()}~{closes.index.max().date()}"
        now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        if not ts_ex.empty:
            rows.append({"rule_type": "trend_stage_excess", "params_json": json.dumps({"horizons": [5, 10, 20], "benchmark": "000300"}, ensure_ascii=False), "metrics_json": ts_metrics(ts_ex), "dataset_range": daterange, "created_at": now})
        if not style_ex.empty:
            rows.append({"rule_type": "style_excess", "params_json": json.dumps({"horizons": [5, 10, 20], "benchmark": "000300"}, ensure_ascii=False), "metrics_json": style_metrics(style_ex), "dataset_range": daterange, "created_at": now})
        if not style_temp_ex.empty:
            rows.append({"rule_type": "style_temp_excess", "params_json": json.dumps({"horizons": [5, 10, 20], "benchmark": "000300", "regimes": "40/60/80"}, ensure_ascii=False), "metrics_json": st_metrics(style_temp_ex), "dataset_range": daterange, "created_at": now})
        if rating_map["n_cells"]:
            rows.append({"rule_type": "rating_position_map", "params_json": json.dumps({"horizons": [10, 20], "mapping": "fwd20-linear-0.05-0.80"}, ensure_ascii=False), "metrics_json": rm_metrics(rating_map), "dataset_range": daterange, "created_at": now})
        if rows:
            upsert_df(conn, "backtest_runs", pd.DataFrame(rows))
    finally:
        conn.close()
    print(f"\n已写入 backtest_runs: {len(rows)} 条")


if __name__ == "__main__":
    main()