"""市场情绪数据：涨停池/炸板池（东财 push2ex，主机轮询）。

炸板池接口仅保留最近 30 个交易日；失败时 zhaban 字段置 None，不阻断。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

_PUSH2EX_HOSTS = [
    "https://push2ex.eastmoney.com",
] + [f"https://{i}.push2ex.eastmoney.com" for i in range(1, 18)]

_UT = "7eea3edcaed734bea9cbfc24409ed989"
_TIMEOUT = 15


def _fetch_pool(endpoint: str, date: str) -> dict:
    last_err = ""
    for host in _PUSH2EX_HOSTS:
        try:
            params = {
                "ut": _UT, "dpt": "wz.ztzt", "Pageindex": "0",
                "pagesize": "10000", "sort": "fbt:asc", "date": date,
            }
            r = requests.get(host + f"/{endpoint}", params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_err = f"{host}: {exc}"
    raise RuntimeError(f"{endpoint} 获取失败: {last_err}")


def build_emotion_df(date: str, zt_rows: list, zb_rows: list | None) -> pd.DataFrame:
    """由涨停/炸板池原始行构造 market_emotion 单行。"""
    limit_up_count = len(zt_rows)
    max_lianban = max((int(r.get("lbc", 0) or 0) for r in zt_rows), default=0)
    if zb_rows is None:
        zhaban_count = None
        zhaban_rate = None
    else:
        zhaban_count = len(zb_rows)
        total = limit_up_count + zhaban_count
        zhaban_rate = round(zhaban_count / total, 4) if total else 0.0
    return pd.DataFrame([{
        "date": date,
        "limit_up_count": limit_up_count,
        "max_lianban": max_lianban,
        "zhaban_count": zhaban_count,
        "zhaban_rate": zhaban_rate,
        "src": "akshare",
    }])


def fetch_emotion(date: str) -> pd.DataFrame:
    """拉取某交易日情绪数据并构造 DataFrame。

    涨停池与炸板池均为空时视为非交易日（节假日/停市），返回空 DataFrame，
    由采集层跳过落库——避免把节假日记成 0 涨停的"极冷"市场。
    """
    zt = _fetch_pool("getTopicZTPool", date)
    zt_rows = ((zt or {}).get("data") or {}).get("pool") or []
    zb_rows: list | None = None
    try:
        zb = _fetch_pool("getTopicZBPool", date)
        zb_rows = ((zb or {}).get("data") or {}).get("pool") or []
    except Exception:
        zb_rows = None
    if not zt_rows and not zb_rows:
        return pd.DataFrame()
    return build_emotion_df(date, zt_rows, zb_rows)


def _to_float(v) -> float | None:
    import math

    try:
        f = float(v)
        return f if not math.isnan(f) else None  # NaN → None
    except (TypeError, ValueError):
        return None


def fetch_limit_up_pool(date: str) -> pd.DataFrame:
    """涨停+炸板池个股明细（2026-08-20，东财 push2ex，盘中实时）。

    涨停池字段：c代码 / n名称 / lbc连板数 / fbt首封时间 / fund封单额；
    炸板池同样字段，zhaban=1 标记。两者均为空视为非交易日，返回空 DataFrame。
    """
    rows: list[dict] = []
    try:
        zt = _fetch_pool("getTopicZTPool", date)
        for r in (((zt or {}).get("data") or {}).get("pool") or []):
            rows.append({
                "date": date,
                "symbol": str(r.get("c", "")),
                "name": r.get("n", ""),
                "lianban": int(r.get("lbc", 0) or 0),
                "first_seal_time": str(r.get("fbt", "") or ""),
                "seal_amount": _to_float(r.get("fund")),
                "zhaban": 0,
                "src": "eastmoney",
            })
    except Exception as exc:
        raise RuntimeError(f"getTopicZTPool 获取失败: {exc}") from exc
    try:
        zb = _fetch_pool("getTopicZBPool", date)
        for r in (((zb or {}).get("data") or {}).get("pool") or []):
            rows.append({
                "date": date,
                "symbol": str(r.get("c", "")),
                "name": r.get("n", ""),
                "lianban": int(r.get("lbc", 0) or 0),
                "first_seal_time": str(r.get("fbt", "") or ""),
                "seal_amount": _to_float(r.get("fund")),
                "zhaban": 1,
                "src": "eastmoney",
            })
    except Exception:
        pass
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def today_str() -> str:
    return dt.date.today().strftime("%Y%m%d")