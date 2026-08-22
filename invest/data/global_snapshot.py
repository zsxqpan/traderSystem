r"""隔夜外围快照（2026-08-21，a-share-market-data 流程落地，进盘前早报）。

数据源（skill 笔记）：
- 美股三大指数：新浪 gb_\$dji / gb_\$ixic / gb_\$inx（GBK）
- 富时A50：新浪 hf_CHA50CFD（0现价 / 7昨收）
- 商品：新浪 hf_CL(原油)/hf_OIL(燃油?)/hf_GC(黄金)/hf_SI(白银)
- 汇率：腾讯 qt.gtimg.cn whUSDCNY（美元人民币）/ whDINIW（美元指数?）

所有源失败仅记录，不抛异常（早报不阻断）。
"""
from __future__ import annotations

import logging
import urllib.request

logger = logging.getLogger(__name__)

# 新浪行情：gb_ 美股 / hf_ 富时·商品；返回 GBK，格式 v_xxx="名称,现价,涨跌额,涨跌幅,..."
_SINA_GLOBAL = {
    "us_dji": "gb_$dji",
    "us_ixic": "gb_$ixic",
    "us_inx": "gb_$inx",
    "a50": "hf_CHA50CFD",
    "oil": "hf_CL",
    "gold": "hf_GC",
    "silver": "hf_SI",
}
_SINA_CODES = ",".join(_SINA_GLOBAL.values())

# 腾讯汇率：whUSDCNY（美元/人民币）
_TENCENT_CODES = "whUSDCNY"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理
    with opener.open(req, timeout=10) as resp:
        return resp.read().decode("gbk", errors="ignore")


def _parse_sina_value(code: str, text: str) -> float | None:
    """按接口类型解析涨跌幅（%）。

    - gb_（美股）：字段 0=名称 1=现价 2=涨跌幅% 3=时间 4=涨跌额 …
    - hf_（期货/富时A50）：字段 0=现价 2=昨收 → 涨跌幅=(现价/昨收-1)*100
    """
    try:
        parts = text.split("~") if "~" in text else text.split(",")
        if code.startswith("hf_"):
            price = float(parts[0])
            prev = float(parts[2])
            return round((price / prev - 1) * 100, 4) if prev else None
        return float(parts[2])
    except (TypeError, ValueError, IndexError):
        return None


def fetch_global_snapshot() -> dict:
    """隔夜外围快照：{us_dji/us_ixic/us_inx/a50/oil/gold/silver: 涨跌幅%, usdcny: 汇率值}。失败项为 None。"""
    out: dict = {k: None for k in _SINA_GLOBAL} | {"usdcny": None}
    try:
        raw = _get(f"https://hq.sinajs.cn/list={_SINA_CODES}")
        for line in raw.split(";"):
            line = line.strip()
            if "=" not in line or "~" not in line and "," not in line:
                continue
            key = line.split("=", 1)[0].strip().replace("var ", "").replace("_hq_str_", "").strip()
            # key 形如 gb_$dji / hf_CHA50CFD
            for name, code in _SINA_GLOBAL.items():
                if code.replace("$", "") in key.replace("$", "") or code in key:
                    out[name] = _parse_sina_value(code, line.split("=", 1)[1].strip().strip('"'))
                    break
    except Exception as exc:
        logger.warning("外围(新浪)快照失败: %s", exc)
    try:
        raw = _get(f"https://qt.gtimg.cn/q={_TENCENT_CODES}")
        for line in raw.split(";"):
            if "whUSDCNY" not in line or "=" not in line:
                continue
            val = line.split("=", 1)[1].strip().strip('"')
            # 腾讯汇率逗号分隔（非股票波浪线格式）
            parts = val.split("~") if "~" in val else val.split(",")
            # 腾讯汇率格式不定：尝试找数值字段
            for p in parts:
                try:
                    v = float(p)
                    if 3 < v < 10:  # USDCNY 合理区间
                        out["usdcny"] = v
                        break
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("外围(腾讯汇率)快照失败: %s", exc)
    return out


_GLOBAL_NAMES = {
    "us_dji": "道指", "us_ixic": "纳指", "us_inx": "标普500",
    "a50": "富时A50", "oil": "原油", "gold": "黄金", "silver": "白银",
}


def global_snapshot_text() -> str:
    """渲染一行外围快照文本（供盘前早报）。全部失败返回空串。"""
    snap = fetch_global_snapshot()
    parts = []
    for k, name in _GLOBAL_NAMES.items():
        v = snap.get(k)
        if v is not None:
            parts.append(f"{name}{v:+.2f}%")
    if snap.get("usdcny"):
        parts.append(f"USDCNY {snap['usdcny']:.4f}")
    return " ".join(parts) if parts else ""
