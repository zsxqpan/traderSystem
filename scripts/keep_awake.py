"""交易日保活脚本（2026-09-08）：由 TraderSystem_power_on 在 08:25 启动。

背景：powercfg /change standby-timeout-ac 0 只能关掉"闲置计时"睡眠，但机器被
WakeToRun 任务唤醒后、若无人值守，约 2 分钟后会被 Windows 自动复睡（09-08 08:26:56
实证，导致 08:30 premarket 漏跑）。本脚本直接持有 ES_SYSTEM_REQUIRED（进程级保活），
可阻止一切自动睡眠（含唤醒后复睡），直到 --until 时刻退出。

用法（由任务以管理员运行，pythonw 无控制台）：
    myenv\\Scripts\\pythonw.exe scripts\\keep_awake.py --until 17:30
"""
import argparse
import ctypes
import datetime
import logging
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_LOG_FILE = ROOT / "logs" / "keep_awake.log"

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)

logger = logging.getLogger("keep_awake")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="17:30", help="保持清醒至该本地时刻，格式 HH:MM")
    args = ap.parse_args()
    hh, mm = (int(x) for x in args.until.split(":"))
    logger.info("keep_awake 启动：保持清醒至 %02d:%02d", hh, mm)
    # 双保险：插电永不休眠（本脚本由提权任务启动，具备管理员权限）
    try:
        r = subprocess.run(
            ["powercfg", "/change", "standby-timeout-ac", "0"],
            capture_output=True, timeout=30,
        )
        logger.info("powercfg standby-timeout-ac 0 -> rc=%s", r.returncode)
    except Exception as exc:
        logger.warning("powercfg 设置失败（不阻断保活）: %s", exc)
    # 持有系统保活：阻止一切自动睡眠
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        while True:
            now = datetime.datetime.now()
            if (now.hour, now.minute) >= (hh, mm):
                break
            import time

            time.sleep(60)
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        logger.info("keep_awake 到达 %02d:%02d，释放保活退出", hh, mm)


if __name__ == "__main__":
    main()
