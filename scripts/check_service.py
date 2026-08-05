"""查看调度服务状态。用法: myenv\\Scripts\\python.exe scripts/check_service.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.db import connect


def main() -> None:
    db = str(ROOT / "data" / "invest.db")
    conn = connect(db)
    try:
        print("最近任务记录（job_runs）:")
        rows = conn.execute(
            """SELECT id, job, status, started_at, finished_at, substr(detail,1,60) AS d
               FROM job_runs ORDER BY id DESC LIMIT 12"""
        ).fetchall()
        for r in rows:
            print(f"  #{r['id']} {r['job']:16} {r['status']:8} start={r['started_at']} {r['d'] or ''}")
        n = conn.execute("SELECT COUNT(*) AS n FROM job_runs WHERE job='scheduler'").fetchone()["n"]
        print(f"\n调度器启动记录数: {n}")
        if n == 0:
            print("（还没有记录 = 修复后的服务尚未启动过；请重启电脑或手动运行 scripts/run_service.py）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()