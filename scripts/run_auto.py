"""TODO [A]11/[A]12 因子与价差自动化 + [A]10 历史快照 手动入口。

用法:
  myenv\\Scripts\\python.exe scripts\\run_auto.py factor      # 候选池四套周期镜像自动打分
  myenv\\Scripts\\python.exe scripts\\run_auto.py universe   # 记录今日历史行业/ST 快照
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.db import connect, init_db


def main() -> None:
    task = sys.argv[1] if len(sys.argv) > 1 else "factor"
    db = str(ROOT / "data" / "invest.db")
    init_db(db)
    conn = connect(db)
    try:
        if task == "factor":
            from invest.discipline.auto import run_pool_automation
            out = run_pool_automation(conn)
            print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
            for cycle, subs in out["results"].items():
                for sym, rep in subs.items():
                    if rep.get("eligible"):
                        print(f"[{cycle}] {sym} 具备错价必要条件（总分 {rep['factor_result']['total']} {rep['factor_result']['grade']}）")
        elif task == "universe":
            from invest.data.universe import record_universe_snapshot
            n = record_universe_snapshot(conn)
            print(f"已记录 {n} 个标的历史行业/ST 快照")
        else:
            print(__doc__)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
