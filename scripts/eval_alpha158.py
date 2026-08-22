"""Alpha158 因子有效性检验（2026-08-16）。

从 daily_bars/index_bars 计算 Alpha158 核心量价因子截面，
用 backtest/factor_eval.py 评估各因子 IC/ICIR/分组单调性，输出 TOP 因子。

用法:
  myenv\\Scripts\\python.exe scripts/eval_alpha158.py [top_n=20] [min_history=120]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from invest.db import connect
from invest.quant.alpha158 import compute_alpha158, factor_names


def main() -> None:
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    min_history = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    db = str(ROOT / "data" / "invest.db")
    conn = connect(db)
    try:
        daily = pd.read_sql_query(
            """SELECT symbol, date, open, high, low, close, volume, amount
               FROM daily_bars ORDER BY symbol, date""", conn,
        )
        idx = pd.read_sql_query(
            """SELECT date, close FROM index_bars WHERE index_code='000300'
               ORDER BY date""", conn,
        )
    finally:
        conn.close()
    if daily.empty:
        print("无 daily_bars 数据（先 scripts/collect.py + scripts/backfill.py 20200101）")
        return
    print(f"计算 Alpha158 因子: {len(factor_names())} 个，标的 {daily['symbol'].nunique()} 个")
    fdf, names = compute_alpha158(daily, idx)
    if fdf.shape[0] < min_history:
        print(f"历史不足（{fdf.shape[0]} < {min_history} 天），因子检验样本偏少")
    print(f"截面: {fdf.shape[0]} 日 × {fdf.shape[1]} 列\n")

    from backtest.factor_eval import factor_eval_report

    # 前向收益（未来 10 日）：用 close 构建 date×symbol
    closes = daily.pivot_table(index="date", columns="symbol", values="close")
    fwd = closes.shift(-10) / closes - 1
    fwd.index = pd.to_datetime(fwd.index, format="mixed", errors="coerce")
    fdf.index = pd.to_datetime(fdf.index, format="mixed", errors="coerce")

    results = []
    for name in names:
        try:
            fac = fdf.xs(name, axis=1, level=0)
            fac.index = pd.to_datetime(fac.index, format="mixed", errors="coerce")
            rep = factor_eval_report(fac, fwd, window=60, n_groups=5)
            if rep.get("ok"):
                results.append({
                    "factor": name,
                    "ic_mean": rep["ic_mean"], "icir": rep["icir"],
                    "ic_positive": rep["ic_positive_pct"],
                    "mono": rep.get("groups", pd.DataFrame()).get("monotonic_hint", "").iloc[-1]
                    if not rep.get("groups", pd.DataFrame()).empty else "",
                })
        except Exception:  # noqa: S112
            continue
    if not results:
        print("无有效因子结果（样本不足或前向收益缺失）")
        return
    rdf = pd.DataFrame(results).sort_values("icir", ascending=False)
    print(f"=== TOP {top_n} 因子（按 ICIR 排序，前向 10 日）===")
    print(rdf.head(top_n).to_string(index=False))
    print("\n=== 有效因子（|IC|>=0.02 且 ICIR>=0.2）===")
    valid = rdf[(rdf["ic_mean"].abs() >= 0.02) & (rdf["icir"] >= 0.2)]
    print(valid.to_string(index=False) if not valid.empty else "(无)")


if __name__ == "__main__":
    main()
