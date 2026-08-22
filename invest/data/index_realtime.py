"""指数实时行情（2026-08-22，B1 盘中报告「盘面总览」用）。

腾讯 qt.gtimg.cn 指数快照：s_sh000001 等，GBK，~ 分割，
[2]=代码 [3]=现价 [4]=涨跌额 → 昨收=现价-涨跌额 → 涨跌幅=涨跌额/昨收。

- 全部失败仅记录返回空 dict（盘面总览省略，不阻断报告）。
- trust_env 绕系统代理（ProxyHandler({})）。
"""
from __future__ import annotations

import logging
import urllib.request

logger = logging.getLogger(__name__)

# 腾讯 A 股指数代码（与 pipeline.INDEX_TENCENT_CODES 同源；这里做盘中实时版）
_INDEX_CODES = {
    "000001": ("s_sh000001", "上证指数"),
    "399001": ("s_sz399001", "深证成指"),
    "000300": ("s_sh000300", "沪深300"),
    "000905": ("s_sh000905", "中证500"),
    "000852": ("s_sh000852", "中证1000"),
    "000688": ("s_sh000688", "科创50"),
    "399006": ("s_sz399006", "创业板指"),
    "899050": ("s_bj899050", "北证50"),
}


def fetch_index_realtime() -> dict[str, dict]:
    """盘中指数实时：{code: {name, price, pct}}。失败返回 {}。"""
    out: dict[str, dict] = {}
    try:
        codes = ",".join(v[0] for v in _INDEX_CODES.values())
        url = f"https://qt.gtimg.cn/q={codes}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/",
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理
        with opener.open(req, timeout=10) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
        for line in raw.split(";"):
            line = line.strip()
            if "=" not in line or "v_pv_none" in line:
                continue
            val = line.split("=", 1)[1].strip().strip('"')
            parts = val.split("~")
            if len(parts) < 5:
                continue
            code = parts[2]
            if code not in _INDEX_CODES:
                continue
            try:
                price = float(parts[3])
                change = float(parts[4])  # 涨跌额（2026-08-22 实测：非昨收价）
            except (TypeError, ValueError):
                continue
            prev = price - change
            if price <= 0 or prev <= 0:
                continue
            out[code] = {
                "name": _INDEX_CODES[code][1],
                "price": price,
                "pct": round(change / prev * 100, 2),
            }
    except Exception as exc:
        logger.warning("指数实时快照失败: %s", exc)
    return out
