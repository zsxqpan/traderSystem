"""回填最近 N 个交易日的市场情绪（涨停池/炸板池）。用法: myenv\\Scripts\\python.exe scripts/backfill_emotion.py [days=60]"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.data.backfill import backfill_emotion


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    db = str(ROOT / "data" / "invest.db")
    print(f"回填最近 {days} 个交易日的市场情绪…（炸板池仅保留近30天）")
    summary = backfill_emotion(db, days)
    ok = sum(1 for r in summary if r["status"] == "ok")
    print(f"成功 {ok}/{len(summary)} 天")
    for r in summary:
        if r["status"] != "ok":
            print(f"  {r['name']}: {r['error'][:100]}")


if __name__ == "__main__":
    main()