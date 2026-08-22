"""行业板块主力资金（2026-08-20，东财行业资金流，a-share-market-data 流程落地）。

数据源：东财 clist——push2 被限流时用 push2delay（skill 笔记：push2delay clist/get f62）。
字段：行业名 f14 / 主力净流入 f62（元）/ 净占比 f184（%）。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

_HOSTS = ["https://push2delay.eastmoney.com", "https://push2.eastmoney.com"]
_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_TIMEOUT = 15


def today_str() -> str:
    return dt.date.today().strftime("%Y%m%d")


def fetch_sector_fund_flow() -> pd.DataFrame:
    """拉取行业板块主力资金排行（今日）：fid=f62 按主力净流入降序，pz=90 全行业。

    返回 DataFrame（date/industry/main_net/main_net_pct/src）。全部 host 失败抛异常。
    """
    params = {
        "pn": "1", "pz": "90", "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f62", "fs": "m:90+t:2", "fields": "f12,f14,f62,f184", "ut": _UT,
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    last_err = ""
    for host in _HOSTS:
        try:
            r = requests.get(host + "/api/qt/clist/get", params=params, headers=headers, timeout=_TIMEOUT)
            r.raise_for_status()
            diff = ((r.json() or {}).get("data") or {}).get("diff") or []
            if not diff:
                continue
            rows = []
            for item in diff:
                try:
                    net = float(item.get("f62") or 0)
                except (TypeError, ValueError):
                    net = 0.0
                try:
                    pct = float(item.get("f184"))
                except (TypeError, ValueError):
                    pct = None
                rows.append({
                    "date": dt.date.today().isoformat(),
                    "industry": str(item.get("f14", "")).strip(),
                    "main_net": net,
                    "main_net_pct": pct,
                    "src": "eastmoney",
                })
            if rows:
                return pd.DataFrame(rows)
        except Exception as exc:
            last_err = f"{host}: {exc}"
    raise RuntimeError(f"板块主力资金获取失败: {last_err}")

