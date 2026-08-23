"""竞价数据（2026-08-22：9:25 集合竞价结束后获取，竞价报告用）。

数据源：
- 指数竞价：腾讯 qt.gtimg.cn（9:25 后现价=竞价价，复用 index_realtime）；
- 全市场竞价高低开榜/量比榜：东财 push2delay clist（fid=f3 涨跌幅 / fid=f50 量比，
  9:25 后返回竞价排序）；
- 连板股/核心关注竞价：腾讯批量行情（[3]=现价 [32]=涨跌幅，9:25 后为竞价数据）。

注意（2026-08-22 踩坑记录）：
- 腾讯指数(s_ ) [4]=涨跌额、个股 [4]=昨收，格式不同；
- 东财 push2delay 直连可用，push2 直连会被断（用 push2delay）；
- 全部失败仅记录返回空，报告省略对应节，不阻断。
"""
from __future__ import annotations

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

# 东财 A 股全市场（沪主板/科创板/深主板/创业板）
_FS_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def _em_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/",
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理
    with opener.open(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _tx_get(codes: str) -> str:
    req = urllib.request.Request(f"https://qt.gtimg.cn/q={codes}", headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/",
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as resp:
        return resp.read().decode("gbk", errors="ignore")


def _market_symbol(symbol: str) -> str:
    """6 位代码 → 腾讯前缀（600/601/603/688=sh，000/001/002/300=sz，8/4=北交所 bj）。"""
    s = (symbol or "").strip()
    if s.startswith(("6", "9")):
        return "sh" + s
    if s.startswith(("0", "3")):
        return "sz" + s
    if s.startswith(("4", "8")):
        return "bj" + s
    return s


def fetch_top_gainers(limit: int = 10) -> list[dict]:
    """竞价高开榜（东财 clist，fid=f3 降序）。失败返回 []。"""
    try:
        url = ("https://push2delay.eastmoney.com/api/qt/clist/get"
               f"?pn=1&pz={limit}&po=1&np=1&fltt=2&invt=2&fid=f3&fs={_FS_A}"
               "&fields=f12,f14,f2,f3")
        diff = (_em_get(url).get("data") or {}).get("diff") or []
        return [{"symbol": str(it.get("f12", "")), "name": str(it.get("f14", "")),
                 "pct": it.get("f3")} for it in diff]
    except Exception as exc:
        logger.warning("竞价高开榜获取失败: %s", exc)
        return []


def fetch_top_losers(limit: int = 10) -> list[dict]:
    """竞价低开榜（fid=f3 升序）。失败返回 []。"""
    try:
        url = ("https://push2delay.eastmoney.com/api/qt/clist/get"
               f"?pn=1&pz={limit}&po=0&np=1&fltt=2&invt=2&fid=f3&fs={_FS_A}"
               "&fields=f12,f14,f2,f3")
        diff = (_em_get(url).get("data") or {}).get("diff") or []
        return [{"symbol": str(it.get("f12", "")), "name": str(it.get("f14", "")),
                 "pct": it.get("f3")} for it in diff]
    except Exception as exc:
        logger.warning("竞价低开榜获取失败: %s", exc)
        return []


def fetch_vol_top(limit: int = 10) -> list[dict]:
    """竞价放量榜（2026-08-22：fid=f5 成交量降序，竞价放量=抢筹信号）。

    注：东财 f50 量比字段在竞价时段返回异常值，改用成交量排序（f5）。
    """
    try:
        url = ("https://push2delay.eastmoney.com/api/qt/clist/get"
               f"?pn=1&pz={limit}&po=1&np=1&fltt=2&invt=2&fid=f5&fs={_FS_A}"
               "&fields=f12,f14,f2,f3,f5")
        diff = (_em_get(url).get("data") or {}).get("diff") or []
        return [{"symbol": str(it.get("f12", "")), "name": str(it.get("f14", "")),
                 "pct": it.get("f3"), "vol": it.get("f5")} for it in diff]
    except Exception as exc:
        logger.warning("竞价放量榜获取失败: %s", exc)
        return []


def fetch_batch_quotes(symbols: list[str]) -> dict[str, dict]:
    """腾讯批量个股竞价行情：{symbol: {name, price, pct, vol}}。失败返回 {}。"""
    symbols = [s for s in (symbols or []) if s]
    if not symbols:
        return {}
    out: dict[str, dict] = {}
    try:
        codes = ",".join(_market_symbol(s) for s in symbols)
        raw = _tx_get(codes)
        for line in raw.split(";"):
            line = line.strip()
            if "=" not in line or "v_pv_none" in line:
                continue
            val = line.split("=", 1)[1].strip().strip('"')
            parts = val.split("~")
            if len(parts) < 33:
                continue
            code = parts[2]  # 6 位代码
            try:
                price = float(parts[3])
                pct = float(parts[32])
                vol = float(parts[6]) if parts[6] else 0.0
            except (TypeError, ValueError):
                continue
            out[code] = {"name": parts[1], "price": price, "pct": pct, "vol": vol}
    except Exception as exc:
        logger.warning("竞价批量行情获取失败: %s", exc)
    return out
