"""AkShare 数据源适配器（免费主源）。

约定：fetch() 只返回数据源原始 DataFrame，不做任何加工；
标准化（normalize）统一由采集编排层调用一次，避免双重转换。
东财接口不可达时自动回退新浪接口。
"""
from __future__ import annotations

import pandas as pd

from .base import BaseSource, SourceError

_RENAME_MAP = {
    "日期": "date", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount",
    "开盘价": "open", "最高价": "high", "最低价": "low", "收盘价": "close",
    "代码": "symbol", "名称": "name",
    "股票代码": "symbol", "上榜日": "date",
    "龙虎榜买入额": "buy", "龙虎榜卖出额": "sell", "龙虎榜净买额": "net",
}

# 需要统一为 YYYY-MM-DD 的 K 线/榜单类 kind（macro_series 是月份、market_emotion 保持原样）
_DATE_KINDS = {
    "daily_bars", "index_bars", "industry_bars", "industry_all",
    "stock_daily_all", "dragon_tiger", "seat_detail", "industry_valuation", "margin",
}

# 各 kind 落库时只保留这些列（避免未知列写入报错）
_KEEP_COLS = {
    "daily_bars": ["date", "symbol", "open", "high", "low", "close", "volume", "amount", "src"],
    "index_bars": ["date", "index_code", "open", "high", "low", "close", "volume", "amount", "src"],
    "dragon_tiger": ["date", "symbol", "name", "seat_type", "buy", "sell", "net", "src"],
    "industry_bars": ["date", "industry", "open", "high", "low", "close", "volume", "amount", "src"],
    "industry_all": ["date", "industry", "open", "high", "low", "close", "volume", "amount", "src"],
    "margin": ["date", "balance", "buy", "src"],
    "macro_series": ["indicator", "date", "value", "unit", "src"],
    "market_emotion": ["date", "limit_up_count", "max_lianban", "zhaban_count", "zhaban_rate", "src"],
    "stock_daily_all": ["date", "symbol", "open", "high", "low", "close", "volume", "amount", "src"],
    "seat_detail": ["date", "symbol", "seat_type", "buy", "sell", "net", "src"],
    "industry_valuation": ["date", "industry", "pe", "level", "src"],
}

# 各 kind 标准化后必须包含的列
_REQUIRED = {
    "daily_bars": ["date", "symbol", "close"],
    "index_bars": ["date", "index_code", "close"],
    "dragon_tiger": ["date", "symbol"],
    "industry_bars": ["date", "industry", "close"],
    "industry_all": ["date", "industry", "close"],
    "margin": ["date", "balance"],
    "macro_series": ["indicator", "date", "value"],
    "market_emotion": ["date"],
    "stock_daily_all": ["date", "symbol", "close"],
    "seat_detail": ["date", "symbol", "seat_type"],
    "industry_valuation": ["date", "industry", "pe"],
}

# 宏观指标 → 单位
_MACRO_UNITS = {
    "pmi": "指数/同比%",
    "shrzgm": "亿元",
    "money_supply": "亿元/同比%",
    "new_financial_credit": "亿元/同比%",
}


def _sina_stock_symbol(symbol: str) -> str:
    """000001 -> sz000001；600xxx -> sh600xxx；4/8 开头 -> bj。"""
    s = symbol.lower()
    if s.startswith(("sh", "sz", "bj")):
        return s
    if s.startswith("6"):
        return "sh" + symbol
    if s.startswith(("4", "8")):
        return "bj" + symbol
    return "sz" + symbol


def _sina_index_symbol(symbol: str) -> str:
    """000300 -> sh000300；399xxx -> sz399xxx；已带前缀则原样。"""
    s = symbol.lower()
    if s.startswith(("sh", "sz", "bj")):
        return s
    return ("sz" if symbol.startswith("399") else "sh") + symbol


def _filter_dates(df: pd.DataFrame, task: dict) -> pd.DataFrame:
    """按 task 的 start_date/end_date（YYYYMMDD 或 YYYY-MM-DD）过滤。"""
    start = task.get("start_date")
    end = task.get("end_date")
    if (not start and not end) or "date" not in df.columns or df.empty:
        return df
    d = pd.to_datetime(df["date"])
    mask = pd.Series(True, index=df.index)
    try:
        if start:
            mask &= d >= pd.to_datetime(start)
        if end:
            mask &= d <= pd.to_datetime(end)
    except Exception:
        return df
    return df[mask]


def _find_date_col(df: pd.DataFrame) -> str | None:
    """在常见日期列名中找第一列，找不到返回 None。"""
    for name in ("月份", "日期", "时间", "统计时间", "date"):
        if name in df.columns:
            return name
    return None


def _macro_to_long(df: pd.DataFrame, task: dict) -> pd.DataFrame:
    """宏观宽表转长表：indicator / date / value / unit / src（幂等）。"""
    if df is None or df.empty:
        return df
    # 幂等保护：已经是长表则直接返回
    if {"indicator", "date", "value"} <= set(df.columns):
        return df
    macro = task.get("macro", "pmi")
    date_col = _find_date_col(df)
    if date_col is None:
        raise ValueError(f"macro {macro}: 未找到日期列，实际列: {list(df.columns)}")
    value_cols = [c for c in df.columns if c != date_col]
    if not value_cols:
        raise ValueError(f"macro {macro}: 无数值列，实际列: {list(df.columns)}")
    unit = _MACRO_UNITS.get(macro, "")
    out = df[[date_col] + value_cols].melt(
        id_vars=[date_col], var_name="indicator", value_name="value"
    )
    out["date"] = out[date_col].astype(str)
    out["unit"] = unit
    out["src"] = "akshare"
    out["indicator"] = out["indicator"].astype(str)
    return out[["indicator", "date", "value", "unit", "src"]]


def call_with_timeout(fn, *args, timeout: float = 25.0):
    """带超时的函数调用（防 akshare 无超时请求卡死）。超时抛 TimeoutError。"""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn, *args).result(timeout=timeout)


class AkShareSource(BaseSource):
    name = "akshare"

    def fetch(self, task: dict) -> pd.DataFrame:
        """返回数据源原始 DataFrame（不做标准化，标准化由编排层调用）。"""
        kind = task["kind"]
        try:
            import akshare as ak
        except ImportError as exc:
            raise SourceError("akshare 未安装（pip install akshare）") from exc

        try:
            if kind == "daily_bars":
                return self._fetch_daily(ak, task)
            if kind == "index_bars":
                return self._fetch_index(ak, task)
            if kind == "dragon_tiger":
                return ak.stock_lhb_detail_em(
                    start_date=task.get("start_date", "20240101"),
                    end_date=task.get("end_date", "20991231"),
                )
            if kind == "industry_all":
                return self._fetch_all_industries(ak, task)
            if kind == "industry_valuation":
                return ak.stock_industry_pe_ratio_cninfo(
                    symbol="国证行业分类",
                    date=task["date"],
                )
            if kind == "seat_detail":
                return self._fetch_seat_detail(ak, task)
            if kind == "stock_daily_all":
                return self._fetch_stock_daily_all(ak, task)
            if kind == "market_emotion":
                from .. import emotion as _emotion
                return _emotion.fetch_emotion(task["date"])
            if kind == "industry_bars":
                # 主源：同花顺行业指数（本网络稳定可达）；东财自实现/akshare 兜底
                try:
                    return ak.stock_board_industry_index_ths(
                        symbol=task["symbol"],
                        start_date=task.get("start_date", "20240101"),
                        end_date=task.get("end_date", "20991231"),
                    )
                except Exception as ths_exc:
                    from .. import industry as _industry
                    try:
                        return _industry.fetch_industry_hist(
                            symbol=task["symbol"],
                            start_date=task.get("start_date", "20240101"),
                            end_date=task.get("end_date", "20991231"),
                        )
                    except Exception as ind_exc:
                        try:
                            return ak.stock_board_industry_hist_em(
                                symbol=task["symbol"],
                                start_date=task.get("start_date", "20240101"),
                                end_date=task.get("end_date", "20991231"),
                                period="日k",
                                adjust="",
                            )
                        except Exception as ak_exc:
                            raise SourceError(
                                f"industry_bars 获取失败（同花顺: {ths_exc}；东财自实现: {ind_exc}；akshare: {ak_exc}）"
                            ) from ak_exc
            if kind == "margin":
                return ak.stock_margin_sse(
                    start_date=task.get("start_date", "20240101"),
                    end_date=task.get("end_date", "20991231"),
                )
            if kind == "macro_series":
                return self._fetch_macro(ak, task)
            raise NotImplementedError(f"akshare 暂未实现 task kind={kind}")
        except NotImplementedError:
            raise
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"akshare {kind} 获取失败: {exc}") from exc

    def _fetch_all_industries(self, ak, task: dict) -> pd.DataFrame:
        """批量行业指数（同花顺）：映射缓存 + 按年直取，失败跳过。"""
        from .. import industry as _industry
        names = task.get("industries") or None
        try:
            return _industry.fetch_ths_industries(
                names=names,
                start_date=task.get("start_date", "20240101"),
                end_date=task.get("end_date", "20991231"),
            )
        except Exception as exc:
            raise SourceError(f"industry_all 获取失败: {exc}") from exc

    def _fetch_seat_detail(self, ak, task: dict) -> pd.DataFrame:
        """候选池个股龙虎榜席位明细（datacenter-web，可达）。"""
        import datetime as _dt
        import time
        symbols = task.get("symbols") or []
        days = int(task.get("days", 10))
        since = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
        frames = []
        errors = []
        for sym in symbols:
            try:
                dates = call_with_timeout(ak.stock_lhb_stock_detail_date_em, symbol=sym, timeout=25)
                if dates is None or dates.empty:
                    continue
                dates["交易日"] = pd.to_datetime(dates["交易日"]).dt.date
                recent = dates[dates["交易日"] >= _dt.date.fromisoformat(since)]
                for d in recent["交易日"]:
                    dstr = d.strftime("%Y%m%d")
                    # 买入/卖出两个方向都取，席位类型更完整（同席位同日两侧出现时后者覆盖）
                    for flag in ("买入", "卖出"):
                        try:
                            df = call_with_timeout(
                                ak.stock_lhb_stock_detail_em, symbol=sym, date=dstr, flag=flag, timeout=25,
                            )
                            if df is None or df.empty:
                                continue
                            df = df.rename(columns={
                                "交易营业部名称": "seat_name",
                                "买入金额": "buy", "卖出金额": "sell", "净额": "net",
                            })
                            df = df[["seat_name", "buy", "sell", "net"]].copy()
                            df["date"] = dstr
                            df["symbol"] = sym
                            frames.append(df)
                        except Exception as exc:  # noqa: BLE001
                            errors.append(f"{sym} {dstr} {flag}: {exc}")
                        time.sleep(0.3)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{sym}: {exc}")
            time.sleep(0.2)
        if not frames:
            raise SourceError("seat_detail 全部失败: " + "; ".join(errors))
        return pd.concat(frames, ignore_index=True)

    def _fetch_stock_daily_all(self, ak, task: dict) -> pd.DataFrame:
        """批量个股日线：逐标的抓取（东财→新浪回退），失败跳过。"""
        import time
        symbols = task.get("symbols") or []
        frames = []
        errors = []
        for sym in symbols:
            try:
                sub = {
                    "kind": "daily_bars", "symbol": sym,
                    "start_date": task.get("start_date", "19900101"),
                    "end_date": task.get("end_date", "20991231"),
                }
                df = self._fetch_daily(ak, sub)
                if df is not None and not df.empty:
                    df = df.copy()
                    df["symbol"] = sym
                    frames.append(df)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{sym}: {exc}")
            time.sleep(0.2)
        if not frames:
            raise SourceError("stock_daily_all 全部失败: " + "; ".join(errors))
        return pd.concat(frames, ignore_index=True)

    def _fetch_macro(self, ak, task: dict) -> pd.DataFrame:
        macro = task.get("macro", "pmi")
        if macro == "pmi":
            return ak.macro_china_pmi()
        if macro == "money_supply":
            # 东财源（列名稳定）；新浪源 macro_china_supply_of_money 列名不稳定，弃用
            return ak.macro_china_money_supply()
        if macro == "new_financial_credit":
            # 东财新增信贷（v1 社融口径替代）
            return ak.macro_china_new_financial_credit()
        if macro == "shrzgm":
            # 商务部源，部分网络 TLS 握手失败；保留供可用网络使用
            return ak.macro_china_shrzgm()
        raise NotImplementedError(f"未知宏观指标 macro={macro}")

    def _fetch_daily(self, ak, task: dict) -> pd.DataFrame:
        """个股日线：优先东财，失败回退新浪。"""
        try:
            return ak.stock_zh_a_hist(
                symbol=task["symbol"],
                period="daily",
                start_date=task.get("start_date", "19900101"),
                end_date=task.get("end_date", "20991231"),
                adjust="qfq",
            )
        except Exception as em_exc:
            try:
                return ak.stock_zh_a_daily(
                    symbol=_sina_stock_symbol(task["symbol"]),
                    adjust="qfq",
                )
            except Exception as sina_exc:
                raise SourceError(
                    f"daily_bars 获取失败（东财: {em_exc}；新浪: {sina_exc}）"
                ) from sina_exc

    def _fetch_index(self, ak, task: dict) -> pd.DataFrame:
        """指数日线：优先东财，失败回退新浪。"""
        try:
            return ak.index_zh_a_hist(
                symbol=task["symbol"],
                period="daily",
                start_date=task.get("start_date", "19900101"),
                end_date=task.get("end_date", "20991231"),
            )
        except Exception as em_exc:
            try:
                return ak.stock_zh_index_daily(symbol=_sina_index_symbol(task["symbol"]))
            except Exception as sina_exc:
                raise SourceError(
                    f"index_bars 获取失败（东财: {em_exc}；新浪: {sina_exc}）"
                ) from sina_exc

    def normalize(self, df: pd.DataFrame, task: dict) -> pd.DataFrame:
        kind = task["kind"]
        if df is None or df.empty:
            return df
        df = df.rename(columns={k: v for k, v in _RENAME_MAP.items() if k in df.columns})
        if "date" in df.columns:
            df["date"] = df["date"].astype(str)
            if kind in _DATE_KINDS:
                # 统一日期格式为 YYYY-MM-DD，避免同一日历日双格式重复（历史混写 20260804/2026-08-04）
                df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")
                df = df.dropna(subset=["date"])

        if kind == "daily_bars":
            df["symbol"] = task["symbol"]
            df["src"] = self.name
        elif kind == "index_bars":
            df["index_code"] = task["symbol"]
            df["src"] = self.name
        elif kind == "dragon_tiger":
            df["src"] = self.name
            # 榜单行无席位概念；非空占位保证 (date,symbol,seat_type,src) 主键可去重，
            # 避免 SQLite NULL 主键导致的重复插入（历史数据由 db._migrate 清理）。
            df["seat_type"] = "list"
        elif kind == "industry_bars":
            df["industry"] = task["symbol"]
            df["src"] = self.name
        elif kind == "industry_all":
            if "行业" in df.columns:
                df = df.rename(columns={"行业": "industry"})
            if "industry" not in df.columns:
                df["industry"] = None
            df["src"] = self.name
        elif kind == "margin":
            df = df.rename(columns={
                "信用交易日期": "date", "融资融券余额": "balance", "融资买入额": "buy",
            })
            if "date" in df.columns:
                df["date"] = df["date"].astype(str)
            df["src"] = self.name
        elif kind == "macro_series":
            df = _macro_to_long(df, task)
        elif kind == "stock_daily_all":
            df["src"] = self.name
        elif kind == "industry_valuation":
            df = df.rename(columns={
                "变动日期": "date", "行业名称": "industry",
                "静态市盈率-加权平均": "pe", "行业层级": "level",
            })
            df["date"] = df["date"].astype(str)
            df["src"] = self.name
        elif kind == "seat_detail":
            if "seat_name" in df.columns:
                df = df.rename(columns={"seat_name": "seat_type"})
            if "seat_type" not in df.columns:
                df["seat_type"] = None
            df["src"] = self.name
        elif kind == "market_emotion":
            if "date" in df.columns:
                df["date"] = df["date"].astype(str)
            if "src" not in df.columns:
                df["src"] = self.name

        required = _REQUIRED.get(kind, [])
        for col in required:
            if col not in df.columns:
                df[col] = None
        keep = _KEEP_COLS.get(kind)
        if keep:
            df = df[[c for c in keep if c in df.columns]]
        return _filter_dates(df, task)