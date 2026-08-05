"""运行定量层（复用 pipeline.quant）。用法: myenv\\Scripts\\python.exe scripts/run_quant.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.pipeline import quant


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "invest.db")
    counts = quant(db)
    print(counts)


if __name__ == "__main__":
    main()