"""数据源适配器基类与异常定义。"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class SourceError(Exception):
    """数据源获取失败（未安装 / 网络 / 接口 / 解析）。"""


class BaseSource(ABC):
    """数据源适配器：按 task 获取数据并统一列名。"""

    name: str = "base"

    @abstractmethod
    def fetch(self, task: dict) -> pd.DataFrame:
        """获取数据；失败抛 SourceError。task 至少含 kind。"""

    def normalize(self, df: pd.DataFrame, task: dict) -> pd.DataFrame:
        """统一列名与类型；子类可覆盖。"""
        return df