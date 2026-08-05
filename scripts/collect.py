"""手动触发一次采集并打印摘要。用法: python scripts/collect.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.data.collector import run_collection


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "invest.db")
    summary = run_collection(db_path)
    for row in summary:
        sources = ", ".join(f"{s['source']}={s['rows']}行" for s in row["sources"]) or "-"
        print(f"{row['name']:14} {row['status']:8} [{sources}] {row['error']}")


if __name__ == "__main__":
    main()