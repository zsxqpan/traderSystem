# -*- coding: utf-8 -*-
"""真实因子评估：从 industry_bars 构造动量/RS 因子面板并检验有效性。

用法: python scripts/eval_factors.py [--horizon 10] [--groups 5]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import argparse

import pandas as pd

from backtest.factor_eval import factor_eval_report
from invest.config import get_settings
from invest.db import connect


def build_factor_panels(conn):
    """从 industry_bars 构造因子面板（date × industry）：
    - mom20: 20 日动量
    - rs20:  20 日相对强度（对 000300）
    """
    ind = pd.read_sql_query("SELECT date, industry, close FROM industry_bars ORDER BY date", conn)
    idx = pd.read_sql_query(
        "SELECT date, close FROM index_bars WHERE index_code='000300' ORDER BY date", conn,
    )
    ind["date"] = pd.to_datetime(ind["date"], format="mixed", errors="coerce")
    idx["date"] = pd.to_datetime(idx["date"], format="mixed", errors="coerce")
    closes = ind.pivot_table(index="date", columns="industry", values="close")
    bench = idx.set_index("date")["close"]

    mom20 = closes / closes.shift(20) - 1
    bench_ret20 = bench / bench.shift(20) - 1
    rs20 = mom20.sub(bench_ret20, axis=0)
    return {"mom20": mom20, "rs20": rs20}


def build_fwd(conn, horizon: int):
    """N 日前向超额收益（date × industry）。"""
    ind = pd.read_sql_query("SELECT date, industry, close FROM industry_bars ORDER BY date", conn)
    idx = pd.read_sql_query(
        "SELECT date, close FROM index_bars WHERE index_code='000300' ORDER BY date", conn,
    )
    ind["date"] = pd.to_datetime(ind["date"], format="mixed", errors="coerce")
    idx["date"] = pd.to_datetime(idx["date"], format="mixed", errors="coerce")
    closes = ind.pivot_table(index="date", columns="industry", values="close")
    bench = idx.set_index("date")["close"]
    bench_fwd = bench.shift(-horizon) / bench - 1
    fwd = closes.shift(-horizon) / closes - 1
    return fwd.sub(bench_fwd, axis=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--groups", type=int, default=5)
    ap.add_argument("--window", type=int, default=60)
    args = ap.parse_args()

    conn = connect(get_settings().db_path)
    factors = build_factor_panels(conn)
    fwd = build_fwd(conn, args.horizon)
    print(f"面板: {len(factors['mom20'].index)} 交易日 x {len(factors['mom20'].columns)} 行业")
    for name, fdf in factors.items():
        r = factor_eval_report(fdf, fwd, window=args.window, n_groups=args.groups)
        if not r["ok"]:
            print(f"\n[{name}] {r['note']}")
            continue
        print(f"\n[{name}] IC={r['ic_mean']} ICIR={r['icir']} 正向占比={r['ic_positive_pct']} 期数={r['n_periods']}")
        print("  结论:", "；".join(r["conclusions"]))
        if not r["groups"].empty:
            g = r["groups"][["group", "mean_ret", "win_rate", "n", "monotonic_hint"]]
            print("  分组:\n" + g.to_string(index=False))
    conn.close()


if __name__ == "__main__":
    main()
