"""测试全局夹具。

1) 默认把外网探测 `invest.data.nethealth.internet_ok` 打桩为 True：
   项目约定「测试全 mock、不连真实网络」，而补偿扫描/采集重试现在会做网络探测，
   不屏蔽的话无网环境（CI）会把任务判成"外网不通"而全部 deferred。
   需要验证网络降级行为的用例，在用例内用 mock.patch 覆盖即可。
2) 把 `invest.data.names.refresh_cache` 打桩为 no-op：盘前任务会按周刷新全市场名称缓存
   （akshare 调用），测试里绝不能真发请求。
3) 静音名单（`invest.mute`）与名称缓存（`invest.data.names.CACHE_FILE`）都改指临时路径：
   测试结果不能被用户真实的 `data/alert_mute.json` / `data/symbol_names.json` 影响。
4) 每个用例前后清空探测缓存与**失败退避状态**（模块级状态，防止用例互相污染）。
"""
from __future__ import annotations

from unittest import mock

import pytest

from invest import mute, scheduler
from invest.data import names, nethealth


@pytest.fixture(autouse=True)
def _network_probe_online(tmp_path_factory):
    nethealth.reset_cache()
    scheduler.reset_job_backoff()
    # 静音名单也用临时文件：测试结果不该被用户真实的 data/alert_mute.json 影响
    mute_file = tmp_path_factory.mktemp("mute") / "alert_mute.json"
    with mock.patch.object(nethealth, "internet_ok", return_value=True), \
         mock.patch.object(names, "refresh_cache", return_value=0), \
         mock.patch.object(mute, "MUTE_FILE", mute_file), \
         mock.patch.object(names, "CACHE_FILE", tmp_path_factory.mktemp("names") / "symbol_names.json"):
        mute.reset_cache()
        yield
    mute.reset_cache()
    nethealth.reset_cache()
    scheduler.reset_job_backoff()
