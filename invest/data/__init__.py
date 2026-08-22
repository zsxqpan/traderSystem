"""数据层：采集、校验、存储、日历。"""
from .collector import TASKS, run_collection

__all__ = ["TASKS", "run_collection"]