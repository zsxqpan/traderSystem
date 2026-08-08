"""Tushare 数据源适配器（备用源，需 token）。"""
from __future__ import annotations

import pandas as pd

from .akshare_source import _KEEP_COLS
from .base import BaseSource, SourceError


def _stock_ts_code(symbol: str) -> str:
    """000001 -> 000001.SZ；600519 -> 600519.SH；4/8 开头 -> BJ。"""
    s = symbol.strip().upper()
    if "." in s:  # 已带交易所后缀则原样
        return s
    if s.startswith(("6", "9", "5")):
        return s + ".SH"
    if s.startswith(("4", "8")):
        return s + ".BJ"
    return s + ".SZ"


def _index_ts_code(symbol: str) -> str:
    """000300 -> 000300.SH；399xxx -> 399xxx.SZ。"""
    s = symbol.strip().upper()
    if "." in s:
        return s
    return (s + ".SZ") if s.startswith("399") else (s + ".SH")


class TushareSource(BaseSource):
    name = "tushare"

    def __init__(self, token: str = ""):
        self.token = token

    def fetch(self, task: dict) -> pd.DataFrame:
        if not self.token:
            raise SourceError("Tushare token 未配置（.env 中 TUSHARE_TOKEN）")
        kind = task["kind"]
        try:
            import tushare as ts
        except ImportError as exc:
            raise SourceError("tushare 未安装（pip install tushare）") from exc

        pro = ts.pro_api(self.token)
        try:
            if kind == "daily_bars":
                df = pro.daily(
                    ts_code=_stock_ts_code(task["symbol"]),
                    start_date=task.get("start_date", "19900101"),
                    end_date=task.get("end_date", "20991231"),
                )
                df = df.rename(columns={"trade_date": "date", "ts_code": "symbol", "vol": "volume"})
            elif kind == "index_bars":
                df = pro.index_daily(
                    ts_code=_index_ts_code(task["symbol"]),
                    start_date=task.get("start_date", "19900101"),
                    end_date=task.get("end_date", "20991231"),
                )
                df = df.rename(columns={"trade_date": "date", "ts_code": "index_code", "vol": "volume"})
            else:
                raise NotImplementedError(f"tushare 暂未实现 task kind={kind}")
        except NotImplementedError:
            raise
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"tushare {kind} 获取失败: {exc}") from exc
        return self.normalize(df, task)

    def normalize(self, df: pd.DataFrame, task: dict) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        df = df.copy()
        df["src"] = self.name
        if task["kind"] == "daily_bars":
            df["symbol"] = task["symbol"]
        elif task["kind"] == "index_bars":
            df["index_code"] = task["symbol"]
        # 统一日期口径为 YYYY-MM-DD（与 akshare 主源一致，避免同表混用两种格式）
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["date"])
        # 只保留落库表需要的列（tushare 返回的 pre_close/pct_chg 等不在 schema 中）
        keep = _KEEP_COLS.get(task["kind"])
        if keep:
            df = df[[c for c in keep if c in df.columns]]
        return df