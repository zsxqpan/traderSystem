"""单任务执行入口（供操作系统计划任务调用，替代 APScheduler 常驻）。

用法: myenv\\Scripts\\python.exe -u scripts/run_job.py <job_name>
可选 job: premarket | morning_brief | after_close | weekend | monthly | yearly
          | industry_refresh | daily_refresh | evening_report

每次运行只执行一个任务（running/ok/failed 留痕 + 失败推送，与 APScheduler 语义一致），
进程跑完即退出，适合 schtasks / cron 等操作系统级定时任务。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.scheduler import run_job_once


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    job = sys.argv[1]
    try:
        run_job_once(job)
        return 0
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        import traceback

        traceback.print_exc(file=sys.stderr)
        print(f"ERROR: {job} 执行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
