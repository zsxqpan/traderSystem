# -*- coding: utf-8 -*-
"""启动常驻调度服务。用法: myenv\\Scripts\\python.exe scripts/run_service.py

单实例互斥（2026-08-17 修复 v2）：
- msvcrt LK_NBLCK 原子占锁，重复启动直接退出（exit 0）。
- 锁文件用 "a+" 打开，绝不截断——"w" 会破坏已持锁实例的锁
  （2026-08-17 曾致双实例同时持锁、任务重复执行）。
- 锁在进程退出时自动释放。
"""
from __future__ import annotations

import msvcrt
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.config import get_settings  # noqa: E402
from invest.scheduler import build_scheduler, log_service_started  # noqa: E402

LOCK_FILE = ROOT / "data" / "service.lock"


def acquire_singleton_lock() -> None:
    """尝试独占锁文件；已占用则退出（exit 0）。锁在进程退出时自动释放。"""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOCK_FILE.exists():
        LOCK_FILE.write_text("", encoding="utf-8")  # 仅首次创建，不覆盖已有内容
    fd = open(LOCK_FILE, "a+")  # 追加模式：绝不截断已有锁内容
    try:
        msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("已有调度服务实例在运行，本实例退出。", file=sys.stderr)
        sys.exit(0)
    # 持有 fd 引用，避免被 GC 关闭导致锁释放
    acquire_singleton_lock._fd = fd  # type: ignore[attr-defined]
    acquire_singleton_lock._pid = os.getpid()  # type: ignore[attr-defined]


def _crash_log(exc_text: str) -> None:
    """把未捕获异常写入 logs/service.log 与 data/service_crash.log，便于事后诊断。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    for p in (ROOT / "logs" / "service.log", ROOT / "data" / "service_crash.log"):
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"\n===== {ts} 服务异常退出 =====\n{exc_text}\n")
        except Exception:  # noqa: BLE001
            pass


def _feishu_ws_worker() -> None:
    """飞书长连接接收器守护线程：异常/断线退出后自动重连。"""
    from invest.push import feishu_ws

    while True:
        try:
            started = feishu_ws.run()  # 阻塞；False=未配置
        except Exception as exc:  # noqa: BLE001
            _crash_log(f"feishu_ws 异常退出: {exc}\n{traceback.format_exc()}")
            print(f"[service] feishu_ws 异常退出: {exc}，30s 后重启")
            time.sleep(30)
            continue
        if not started:
            return  # 未配置，不重试
        print("[service] feishu_ws 连接退出，10s 后重连")
        time.sleep(10)


def _start_feishu_ws() -> None:
    """按配置启动飞书长连接接收器（守护线程）。"""
    settings = get_settings()
    if not (
        settings.feishu_app_id
        and settings.feishu_app_secret
        and settings.feishu_chat_id
        and settings.feishu_owner_open_id
    ):
        return
    t = threading.Thread(target=_feishu_ws_worker, name="feishu-ws", daemon=True)
    t.start()
    print("[service] 飞书长连接接收器已启动（项目本体直连）")


def main() -> None:
    acquire_singleton_lock()
    log_service_started()
    # 2026-08-18：默认 OS 计划任务模式（--ticker-only，只跑盘中 10s 轮询 + 飞书接收）；
    # 显式加 --full 才启用完整 APScheduler（不装 OS 任务的旧模式）
    ticker_only = "--full" not in sys.argv
    sched = build_scheduler(ticker_only=ticker_only)
    sched.start()
    if ticker_only:
        print(f"调度服务已启动(pid={os.getpid()})：ticker-only 模式（默认）——仅盘中 10s 轮询 + 飞书接收，"
              f"定时任务由 OS 计划任务承担；如需完整 APScheduler 请加 --full")
    else:
        print(f"调度服务已启动(pid={os.getpid()})：完整 APScheduler 模式（--full）——盘前08:30 / 盘后16:00 / 周末周日20:00 / 夜间22:00")
    _start_feishu_ws()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sched.shutdown(wait=False)
    except Exception:  # noqa: BLE001
        # 主循环异常：落盘 traceback 后重抛，便于定位（2026-08-18 加固）
        _crash_log(traceback.format_exc())
        sched.shutdown(wait=False)
        raise


if __name__ == "__main__":
    main()