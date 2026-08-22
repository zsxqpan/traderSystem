r"""隔夜外围快照（2026-08-21，a-share-market-data 流程落地，进盘前早报）。

数据源（skill 笔记）：
- 美股三大指数：新浪 gb_\$dji / gb_\$ixic / gb_\$inx（GBK）
- 富时A50：新浪 hf_CHA50CFD（0现价 / 7昨收）
- 商品：新浪 hf_CL(原油)/hf_OIL(燃油?)/hf_GC(黄金)/hf_SI(白银)
- 汇率：腾讯 qt.gtimg.cn whUSDCNY（美元人民币）/ whDINIW（美元指数?）
- 日经225/韩国KOSPI（2026-08-22 新增）：东财 push2delay ulist
  （secid 100.N225 / 100.KS11，当日实测可用；日韩提前于 A 股开盘）

所有源失败仅记录，不抛异常（早报不阻断）。
"""
from __future__ import annotations

import json
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

# 东财 push2delay 国际指数（2026-08-22 新增，日韩提前开盘，8:40 盘前可拿当日行情）
_EM_INTL = {
    "jp_nikkei": ("100.N225", "日经225"),
    "kr_kospi": ("100.KS11", "韩国KOSPI"),
}


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


def _fetch_em_intl() -> dict:
    """东财 push2delay 国际指数（日经225/韩国KOSPI）：{key: 涨跌幅%}。失败项为 None。"""
    out: dict = {k: None for k in _EM_INTL}
    try:
        secids = ",".join(v[0] for v in _EM_INTL.values())
        url = ("https://push2delay.eastmoney.com/api/qt/ulist.np/get"
               f"?secids={secids}&fields=f2,f3,f4,f12,f14&fltt=2&invt=2")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/",
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理
        with opener.open(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
        diff = (data.get("data") or {}).get("diff") or []
        for item in diff:
            code = item.get("f12") or ""
            for key, (secid, _name) in _EM_INTL.items():
                if secid.endswith(code):
                    try:
                        out[key] = float(item["f3"])  # 涨跌幅%
                    except (TypeError, KeyError, ValueError):
                        out[key] = None
                    break
    except Exception as exc:
        logger.warning("外围(东财国际指数)快照失败: %s", exc)
    return out


def fetch_global_snapshot() -> dict:
    """隔夜外围快照：{us_dji/us_ixic/us_inx/a50/oil/gold/silver/jp_nikkei/kr_kospi: 涨跌幅%, usdcny: 汇率值}。失败项为 None。"""
    out: dict = {k: None for k in _SINA_GLOBAL} | {"usdcny": None, "jp_nikkei": None, "kr_kospi": None}
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
    # 东财日韩（提前开盘，2026-08-22）
    out.update(_fetch_em_intl())
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


def global_snapshot_rows() -> list[dict]:
    """外围表格行（2026-08-22，盘前报告表格用）：[{name, pct}，含日韩]，失败项省略。"""
    snap = fetch_global_snapshot()
    order = ("us_dji", "us_ixic", "us_inx", "a50", "jp_nikkei", "kr_kospi",
             "oil", "gold", "silver")
    rows: list[dict] = []
    for k in order:
        name = _GLOBAL_NAMES.get(k) or _EM_INTL.get(k, ("", ""))[1]
        v = snap.get(k)
        if v is not None:
            rows.append({"name": name, "pct": v})
    if snap.get("usdcny"):
        rows.append({"name": "USDCNY", "pct": None, "value": snap["usdcny"]})
    return rows
