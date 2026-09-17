"""外网连通性探测（2026-09-15 引入）。

背景（2026-09-15 事故）：下午本机外网中断约 5 小时（东财/新浪/同花顺/飞书/企微/雪球
全部 TCP 超时），而补偿扫描仍每分钟重试 —— `snapshot_close` 白跑 42 次，每次都在等
TCP 超时，日志刷屏、机器空耗；网络 20:10 恢复后窗口（17:29:59）已过，什么都没补。

本模块提供**进程内缓存**的轻量探测（纯 TCP connect，不发 HTTP），供两条链路短路：
- `scheduler`：外网不通时补偿扫描直接 deferred（不跑正文、不进退避计数）；
- `collector`：重试前探测，不通就放弃重试（避免把超时当成数据源故障）。

设计要点：
- **任一探测点连通即视为可达**（单站点故障不该判定整网不通）；
- 结果带缓存（通 60s / 不通 20s），避免每轮扫描都重复探测；
- 探测失败自身不抛异常（调用方按"不可达"处理即可），也不阻塞业务主流程。
"""
from __future__ import annotations

import logging
import socket
import time

logger = logging.getLogger(__name__)

# 覆盖采集（东财/新浪）与推送（飞书）两条链路；任一可达即通过
PROBE_HOSTS: tuple[tuple[str, int], ...] = (
    ("push2his.eastmoney.com", 443),
    ("finance.sina.com.cn", 443),
    ("open.feishu.cn", 443),
    ("www.baidu.com", 443),
)
PROBE_TIMEOUT = 2.0
CACHE_TTL_OK = 60.0     # 通：60s 内不再探测
CACHE_TTL_FAIL = 20.0   # 不通：20s 后重探（网络刚恢复能尽快放行）

_cache: tuple[float, bool] | None = None


def internet_ok(timeout: float = PROBE_TIMEOUT, *, force: bool = False) -> bool:
    """外网是否可达。带进程内缓存；`force=True` 强制重新探测。"""
    global _cache
    now = time.monotonic()
    if not force and _cache is not None:
        ttl = CACHE_TTL_OK if _cache[1] else CACHE_TTL_FAIL
        if now - _cache[0] < ttl:
            return _cache[1]

    reachable = False
    for host, port in PROBE_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                reachable = True
                break
        except OSError:
            continue
    if reachable != (_cache[1] if _cache else None):
        if reachable:
            logger.info("外网探测恢复：可达")
        else:
            logger.warning(
                "外网探测失败：%d 个探测点均不可达，采集/推送将暂停重试（恢复后自动补跑）",
                len(PROBE_HOSTS),
            )
    _cache = (now, reachable)
    return reachable


def reset_cache() -> None:
    """清空缓存（测试用）。"""
    global _cache
    _cache = None
