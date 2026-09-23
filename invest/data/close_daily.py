"""收盘即日线（2026-08-24）：东财 push2delay clist 全市场批量快照 → 当日完整 OHLCV。

背景：akshare 东财日线（push2his kline 接口）在本机存在 RemoteDisconnected 且当日数据
更新偏晚（约晚间 21 点后）；而东财**行情列表接口**（clist，push2delay）收盘后立即返回
全市场当日 OHLCV（f2 最新=f2 收盘价 / f17 今开 / f15 最高 / f16 最低 / f5 成交量手 / f6 成交额元）。
- 分页拉取：clist pz 上限 100，全 A 约 5550+ 只 → ~56 页（单页失败重试+跳过，见 _PAGE_ATTEMPTS）；
- 收盘后（16:10 snapshot_close 任务）调用，写入 daily_bars(src='snapshot')；
  晚间 akshare 权威数据写入后删除当日 snapshot 行（见 collector._run_one）。
- 失败静默返回空 DataFrame（不阻断 snapshot_close 其他逻辑）。
"""
from __future__ import annotations

import logging
import time

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_HOSTS = ["https://push2delay.eastmoney.com", "https://push2.eastmoney.com"]
_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_TIMEOUT = 15
_PAGE_SIZE = 100
# 2026-09-23：单页失败不再直接放弃分页——当天第 8 页 RemoteDisconnected 就 break，
# 全市场只拿到 700/5200 只（快照残缺）。现在单页重试 2 轮（两主机各一次），
# 跳过后继续拉后续页；连续失败 5 页才收工。
_PAGE_ATTEMPTS = 2
_MAX_PAGE_FAILS = 5
_MIN_COVERAGE = 0.95
_FIELDS = "f2,f3,f5,f6,f12,f14,f15,f16,f17,f18"
# 沪深京 A 股
_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def _f(v) -> float | None:
    """宽松转 float；空/'-' 返回 None。"""
    try:
        if v is None or v == "" or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_page(host: str, pn: int) -> dict | None:
    params = {
        "pn": pn, "pz": _PAGE_SIZE, "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f3", "fs": _FS, "fields": _FIELDS, "ut": _UT,
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    r = requests.get(host + "/api/qt/clist/get", params=params, headers=headers, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_all_close_daily(date: str | None = None) -> pd.DataFrame:
    """收盘后全市场当日日线（OHLCV）。

    返回 DataFrame：date/symbol/open/high/low/close/volume/amount（volume 单位=手）。
    失败/无数据返回空 DataFrame。date 缺省为今天 ISO。
    """
    import datetime as dt

    day = (date or dt.date.today().isoformat())
    rows: list[dict] = []
    last_err = ""
    total: int | None = None
    pn = 1
    failed_pages = 0
    while True:
        page = None
        for attempt in range(1, _PAGE_ATTEMPTS + 1):
            for host in _HOSTS:
                try:
                    page = _fetch_page(host, pn)
                    break
                except Exception as exc:
                    last_err = f"{host}: {exc}"
            if page is not None:
                break
            if attempt < _PAGE_ATTEMPTS:
                time.sleep(0.5 * attempt)
        if page is None:
            failed_pages += 1
            logger.warning("收盘日线 clist 第 %d 页失败（连续 %d 页）: %s", pn, failed_pages, last_err)
            if failed_pages >= _MAX_PAGE_FAILS:
                break
            pn += 1
            time.sleep(0.2)
            continue
        failed_pages = 0
        data = page.get("data") or {}
        if total is None:
            total = int(data.get("total") or 0)
        diff = data.get("diff") or []
        for it in diff:
            close = _f(it.get("f2"))
            open_ = _f(it.get("f17"))
            high = _f(it.get("f15"))
            low = _f(it.get("f16"))
            code = it.get("f12")
            if not code or close is None or close <= 0:
                continue
            rows.append({
                "date": day, "symbol": str(code),
                "open": open_, "high": high, "low": low, "close": close,
                "volume": _f(it.get("f5")), "amount": _f(it.get("f6")),
            })
        if not diff or len(diff) < _PAGE_SIZE or (total and pn * _PAGE_SIZE >= total):
            break
        pn += 1
        time.sleep(0.1)
    if not rows:
        logger.warning("收盘日线无数据（total=%s, last_err=%s）", total, last_err)
        return pd.DataFrame()
    if total and len(rows) < total * _MIN_COVERAGE:
        logger.warning("收盘日线仅取到 %d/%s 行（分页部分失败，非全市场）", len(rows), total)
    logger.info("收盘日线全市场获取 %d 行（total=%s）", len(rows), total)
    return pd.DataFrame(rows)
