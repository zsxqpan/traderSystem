"""数据层：采集、校验、存储、日历。"""
from .collector import TASKS, run_collection  # noqa: F401

__all__ = ["TASKS", "run_collection"]