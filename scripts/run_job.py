"""单任务执行入口（供操作系统计划任务调用，替代 APScheduler 常驻）。

用法: myenv\\Scripts\\python.exe -u scripts/run_job.py <job_name>
可选 job: premarket | morning_brief | auction | snapshot_close | after_close
          | pool_trap_scan | weekend | monthly | yearly | industry_refresh
          | daily_refresh | factcard_refresh | evening_report

每次运行只执行一个任务（job_executions 持久幂等 + job_runs 兼容留痕）。
业务返回 False、推送失败、执行异常或 missed 均返回非零；竞价仅在
09:25:30-09:29:30 交易日窗口内允许补跑，过窗只告警不使用盘中数据补发。
同槽任务等待 30 秒仍未结束返回临时失败码 75；仅最终 ok/already_ok 返回 0。
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
        result = run_job_once(job, wait_for_running=30.0)
        if result.status == "deferred":
            print(f"DEFERRED: {job}: {result.detail}", file=sys.stderr)
            return 75
        if result.status not in {"ok", "already_ok"}:
            print(f"ERROR: {job} 未成功: {result.status} {result.detail}", file=sys.stderr)
            return 1
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
