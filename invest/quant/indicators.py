"""指标参数注册表：代码默认值 + config.yaml indicators 段（yaml 优先）。"""
from __future__ import annotations

from functools import lru_cache

from invest.config import load_yaml_config

DEFAULT_PARAMS = {
    "strength": {
        "rs_windows": [5, 10, 20],
        "rs_weights": [0.2, 0.3, 0.5],
        "momentum_windows": [5, 10, 20],
    },
    "rotation": {
        "rank_change_threshold": 3,
    },
    "temperature": {
        "momentum_window": 5,
        "top_n": 5,
    },
    "weekly_strength": {
        "rs_windows": [4, 12],
        "rs_weights": [0.4, 0.6],
        "momentum_windows": [4, 12],
    },
    "crowding": {
        "window": 250,
    },
    "linkage": {
        "corr_window": 60,
        "corr_threshold": 0.7,
    },
}


@lru_cache
def _yaml_params() -> dict:
    """config/config.yaml 的 indicators 段（键名与代码 get_params 一致）。"""
    cfg = load_yaml_config()
    return cfg.get("indicators", {}) or {}


def get_params(section: str, overrides: dict | None = None) -> dict:
    """返回某指标参数：默认值 < config.yaml < 调用方覆盖。"""
    params = dict(DEFAULT_PARAMS.get(section, {}))
    params.update(_yaml_params().get(section, {}))
    if overrides:
        params.update(overrides)
    return params