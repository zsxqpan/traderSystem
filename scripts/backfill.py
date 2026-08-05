"""历史数据回填。用法: myenv\\Scripts\\python.exe scripts/backfill.py [start_date]"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.data.backfill import run_backfill


def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else "20200101"
    db = str(ROOT / "data" / "invest.db")
    print(f"回填开始: start={start}（日线/沪深300/16行业，行业较慢约3-6分钟）")
    summary = run_backfill(db, start)
    for r in summary:
        sources = "; ".join(f"{s['source']}={s['rows']}行" for s in r["sources"]) or "-"
        print(f"{r['name']:14} {r['status']:8} [{sources}] {r['error'][:120]}")
    print("回填完成")


if __name__ == "__main__":
    main()