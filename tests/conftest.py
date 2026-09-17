"""测试全局夹具。

1) 默认把外网探测 `invest.data.nethealth.internet_ok` 打桩为 True：
   项目约定「测试全 mock、不连真实网络」，而补偿扫描/采集重试现在会做网络探测，
   不屏蔽的话无网环境（CI）会把任务判成"外网不通"而全部 deferred。
   需要验证网络降级行为的用例，在用例内用 mock.patch 覆盖即可。
2) 每个用例前后清空探测缓存与**失败退避状态**（模块级状态，防止用例互相污染）。
"""
from __future__ import annotations

from unittest import mock

import pytest

from invest import scheduler
from invest.data import nethealth


@pytest.fixture(autouse=True)
def _network_probe_online():
    nethealth.reset_cache()
    scheduler.reset_job_backoff()
    with mock.patch.object(nethealth, "internet_ok", return_value=True):
        yield
    nethealth.reset_cache()
    scheduler.reset_job_backoff()
