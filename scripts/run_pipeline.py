"""手动执行单段流水线。用法: myenv\\Scripts\\python.exe scripts/run_pipeline.py <collect|quant|premarket|after_close|weekend>"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import invest.pipeline as pl


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "after_close"
    db = str(ROOT / "data" / "invest.db")
    if stage == "collect":
        print(pl.collect(db))
    elif stage == "quant":
        print(pl.quant(db))
    elif stage == "premarket":
        print(pl.notify_premarket(db, pl.agent_premarket(db)))
    elif stage == "after_close":
        text = pl.agent_after_close(db)
        n = pl.arbitrate_all(db)
        print(pl.notify_after_close(db, text if n == 0 else f"{text}\n[仲裁 {n} 对]"))
    elif stage == "weekend":
        print(pl.notify_weekend(db))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()