"""回填行业 PE 历史（近 N 年每月末）。用法: myenv\\Scripts\\python.exe scripts/backfill_valuation.py [years=5]"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.data.backfill import backfill_valuation


def main() -> None:
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    db = str(ROOT / "data" / "invest.db")
    print(f"回填近 {years} 年行业 PE（每月末，约 {years * 12} 次请求）…")
    summary = backfill_valuation(db, years)
    ok = sum(1 for r in summary if r["status"] == "ok")
    print(f"成功 {ok}/{len(summary)} 个月")
    for r in summary:
        if r["status"] != "ok":
            print(f"  {r['name']}: {r['error'][:100]}")


if __name__ == "__main__":
    main()