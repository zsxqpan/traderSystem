"""启动常驻调度服务。用法: myenv\\Scripts\\python.exe scripts/run_service.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.scheduler import build_scheduler, log_service_started


def main() -> None:
    log_service_started()
    sched = build_scheduler()
    sched.start()
    print("调度服务已启动：盘前08:30 / 盘后16:00 / 周末09:00 / 夜间22:00")
    try:
        import time
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sched.shutdown(wait=False)


if __name__ == "__main__":
    main()