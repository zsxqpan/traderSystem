"""东方财富行业板块：列表与历史 K 线（自实现）。

动机：akshare 硬编码主机在部分网络（含本机代理）下不可达；
代理还会掐断大响应，因此 K 线按时间窗分片抓取并做主机轮询+重试。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

_PUSH2_HOSTS = [
    "https://push2.eastmoney.com",
    "https://1.push2.eastmoney.com",
    "https://2.push2.eastmoney.com",
    "https://3.push2.eastmoney.com",
    "https://5.push2.eastmoney.com",
    "https://7.push2.eastmoney.com",
    "https://10.push2.eastmoney.com",
    "https://17.push2.eastmoney.com",
]
_PUSH2HIS_HOSTS = [
    "https://push2his.eastmoney.com",
] + [f"https://{i}.push2his.eastmoney.com" for i in range(1, 18)]

_TIMEOUT = 20
_CACHE_TTL_DAYS = 7
_WINDOW_DAYS = 180  # 约 6 个月，规避代理对大响应的截断

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "industry_list.json"

_KLINE_COLUMNS = [
    "日期", "开盘", "收盘", "最高", "最低",
    "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率",
]

# 记录最近一次成功的 K 线主机，优先使用（跨进程持久化）
_GOOD_KLINE_HOST: str | None = None
_HOST_CACHE = Path(__file__).resolve().parents[2] / "data" / "kline_host.json"


def _load_good_host() -> str | None:
    try:
        if _HOST_CACHE.exists():
            with open(_HOST_CACHE, "r", encoding="utf-8") as f:
                host = json.load(f).get("host")
            return host if host in _PUSH2HIS_HOSTS else None
    except Exception:
        pass
    return None


def _save_good_host(host: str) -> None:
    try:
        _HOST_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(_HOST_CACHE, "w", encoding="utf-8") as f:
            json.dump({"host": host}, f)
    except Exception:
        pass


def _cache_valid(cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    try:
        import datetime as dt
        age_days = (dt.datetime.now() - dt.datetime.fromtimestamp(cache_path.stat().st_mtime)).days
        return age_days < _CACHE_TTL_DAYS
    except Exception:
        return False


def fetch_industry_list(cache_path: Path | None = None, force: bool = False) -> pd.DataFrame:
    """返回 code/name 行业表；优先读本地缓存，缓存失效时轮询主机拉取并刷新缓存。"""
    cache_path = cache_path or DEFAULT_CACHE
    if not force and _cache_valid(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return pd.DataFrame(json.load(f))

    last_err = ""
    for host in _PUSH2_HOSTS:
        try:
            params = {
                "pn": "1", "pz": "200", "po": "1", "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": "m:90 t:2 f:!50",
                "fields": "f12,f14",
            }
            r = requests.get(host + "/api/qt/clist/get", params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            diff = ((data or {}).get("data") or {}).get("diff") or []
            if not diff:
                last_err = f"{host}: 空数据"
                continue
            df = pd.DataFrame(diff)[["f12", "f14"]]
            df.columns = ["code", "name"]
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(df.to_dict(orient="records"), f, ensure_ascii=False)
            return df
        except Exception as exc:
            last_err = f"{host}: {exc}"
    raise RuntimeError(f"行业列表获取失败: {last_err}")


def _split_windows(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """把 YYYYMMDD 区间按 _WINDOW_DAYS 天切分。"""
    import datetime as dt
    start = dt.datetime.strptime(start_date, "%Y%m%d").date()
    end = dt.datetime.strptime(end_date, "%Y%m%d").date()
    windows = []
    cur = start
    while cur <= end:
        win_end = min(cur + dt.timedelta(days=_WINDOW_DAYS - 1), end)
        windows.append((cur.strftime("%Y%m%d"), win_end.strftime("%Y%m%d")))
        cur = win_end + dt.timedelta(days=1)
    return windows


def _kline_request(host: str, params: dict, retries: int = 2) -> list[str]:
    """请求单个主机；成功返回 klines 列表，失败抛 RuntimeError。"""
    last = ""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(host + "/api/qt/stock/kline/get", params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            klines = ((data or {}).get("data") or {}).get("klines") or []
            if not klines:
                last = f"{host}: 空数据"
                continue
            return klines
        except Exception as exc:
            last = f"{host}(第{attempt}次): {exc}"
    raise RuntimeError(last)


_THS_MAP_CACHE = Path(__file__).resolve().parents[2] / "data" / "ths_industry_map.json"


def load_ths_map(force: bool = False) -> dict[str, str]:
    """同花顺行业名称→代码映射（本地缓存 7 天，避免每次重复拉取）。"""
    if not force and _cache_valid(_THS_MAP_CACHE):
        with open(_THS_MAP_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    from akshare.stock_feature.stock_board_industry_ths import (
        _get_stock_board_industry_name_ths,
    )
    m = _get_stock_board_industry_name_ths()
    _THS_MAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(_THS_MAP_CACHE, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    return m


def _parse_ths_year_text(text: str) -> pd.DataFrame:
    """同花顺年度 js 文本 → K线（日期/开盘价/最高价/最低价/收盘价/成交量/成交额）。"""
    start = text.find("{")
    if start < 0:
        raise ValueError("js 无数据")
    body = text[start:-1] if text.endswith(")") else text[start:]
    try:
        import demjson
        data = demjson.decode(body)
    except Exception:
        import json as _json
        data = _json.loads(body[: body.rfind("}") + 1])
    rows = [r.split(",") for r in str(data.get("data", "")).split(";") if r]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).iloc[:, :7]
    df.columns = ["日期", "开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"]
    df["日期"] = pd.to_datetime(df["日期"], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")
    return df.dropna(subset=["日期"])


def _top_up_ths_latest(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """用 akshare 同花顺接口补齐 js 数据缺少的最新交易日。

    年度 js 数据在交易日当天 16:00 前后常滞后一天；akshare 的同花顺接口
    与 js 是同一指数序列（数值口径一致），可安全补齐，不跨源混用。
    返回与 _parse_ths_year_text 同列的 DataFrame，失败返回空表。
    """
    if df is None or df.empty:
        return pd.DataFrame()
    import datetime as dt

    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()
    latest = pd.to_datetime(df["日期"]).max()
    start = (latest + dt.timedelta(days=1)).strftime("%Y%m%d")
    end = dt.date.today().strftime("%Y%m%d")
    if start > end:
        return pd.DataFrame()
    try:
        extra = ak.stock_board_industry_index_ths(symbol=name, start_date=start, end_date=end)
    except Exception:
        return pd.DataFrame()
    if extra is None or extra.empty:
        return pd.DataFrame()
    extra = extra.copy()
    extra["日期"] = pd.to_datetime(extra["日期"], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")
    return extra.dropna(subset=["日期"])


def _concat_industry_years(parts: list[pd.DataFrame]) -> pd.DataFrame:
    """拼接各年度 K 线：去重（年份文件存在跨年重叠）+ 按日期排序。"""
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    return df.drop_duplicates(subset=["日期"]).sort_values("日期").reset_index(drop=True)


def fetch_ths_industries(
    names: list[str] | None = None,
    start_date: str = "20240101",
    end_date: str = "20991231",
    delay: float = 0.1,
) -> pd.DataFrame:
    """批量同花顺行业指数：映射拉一次，按年直取 js，逐行业跳过失败。"""
    import datetime as dt
    import time

    import py_mini_racer
    import requests
    from akshare.stock_feature.stock_board_industry_ths import _get_file_content_ths

    code_map = load_ths_map()
    names = names or list(code_map.keys())
    js_code = py_mini_racer.MiniRacer()
    js_code.eval(_get_file_content_ths("ths.js"))
    v_code = js_code.call("v")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "http://q.10jqka.com.cn",
        "Host": "d.10jqka.com.cn",
        "Cookie": f"v={v_code}",
    }
    begin_year = int(start_date[:4])
    current_year = dt.date.today().year
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for name in names:
        code = code_map.get(name)
        if not code:
            errors.append(f"{name}: 无代码")
            continue
        try:
            parts = []
            for year in range(begin_year, current_year + 1):
                url = f"https://d.10jqka.com.cn/v4/line/bk_{code}/01/{year}.js"
                r = requests.get(url, headers=headers, timeout=15)
                r.raise_for_status()
                dfy = _parse_ths_year_text(r.text)
                if not dfy.empty:
                    parts.append(dfy)
            if not parts:
                errors.append(f"{name}: 空数据")
                continue
            df = _concat_industry_years(parts)
            extra = _top_up_ths_latest(name, df) if not df.empty else pd.DataFrame()
            if not extra.empty:
                df = _concat_industry_years([df, extra])
            df["行业"] = name
            frames.append(df)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
        time.sleep(delay)
    if not frames:
        raise RuntimeError("同花顺行业全量获取失败: " + "; ".join(errors))
    return pd.concat(frames, ignore_index=True)


def fetch_industry_hist(
    symbol: str,
    start_date: str = "20240101",
    end_date: str = "20991231",
    cache_path: Path | None = None,
) -> pd.DataFrame:
    """拉取行业板块日 K（分窗 + 主机轮询 + 重试）。symbol 支持 BK 代码或名称。"""
    global _GOOD_KLINE_HOST

    code = symbol
    if not symbol.upper().startswith("BK"):
        lst = fetch_industry_list(cache_path=cache_path)
        matched = lst[lst["name"] == symbol]
        if matched.empty:
            raise ValueError(f"行业板块不存在: {symbol}")
        code = matched.iloc[0]["code"]

    frames = []
    for win_start, win_end in _split_windows(start_date, end_date):
        params = {
            "secid": f"90.{code}",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101", "fqt": "0",
            "beg": win_start, "end": win_end,
            "lmt": "1000000",
        }
        persisted = _load_good_host() or _GOOD_KLINE_HOST
        hosts = list(dict.fromkeys(
            [h for h in [persisted] + [_GOOD_KLINE_HOST] + _PUSH2HIS_HOSTS if h]
        ))
        klines = None
        last_err = ""
        for host in hosts:
            try:
                klines = _kline_request(host, params)
                _GOOD_KLINE_HOST = host
                _save_good_host(host)
                break
            except Exception as exc:
                last_err = f"{host}: {exc}"
                time.sleep(0.3)
        if klines is None:
            raise RuntimeError(f"{symbol} {win_start}-{win_end}: K线获取失败: {last_err}")
        time.sleep(0.5)
        if klines is None:
            raise RuntimeError(f"{symbol} {win_start}-{win_end}: K线获取失败: {last_err}")
        frames.append(pd.DataFrame([line.split(",") for line in klines], columns=_KLINE_COLUMNS))

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["日期"]).sort_values("日期").reset_index(drop=True)
    return df