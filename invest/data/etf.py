"""ETF 数据（2026-08-22：盘面总览/板块分析用）。

akshare fund_etf_spot_em 一次拉全市场 ETF 实时/收盘行情，按配置过滤返回。
字段：最新价/涨跌幅/成交额/换手率/量比/主力净流入(净额/占比)/超大单净流入。

- 指数 ETF 量能异常（量比高 / 超大单净流入大）→ 大资金进出信号（如国家队）；
- ETF 纯度高于板块指数，用于验证板块方向强度（用户指定逻辑）；
- 失败返回 {}（报告省略 ETF 节，不阻断）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 主要指数 ETF（盘面总览）
INDEX_ETFS = {
    "510300": "沪深300ETF",
    "510050": "上证50ETF",
    "510500": "中证500ETF",
    "512100": "中证1000ETF",
    "159915": "创业板ETF",
    "588000": "科创50ETF",
    "510310": "沪深300ETF易方达",
    "563000": "中证A500ETF",
}

# 重要板块 → 代表 ETF（用户指定方向；纯度高于板块指数，更能体现方向变化）
SECTOR_ETFS = {
    "AI硬件": ["512480", "515880", "159995"],   # 半导体 / 通信 / 芯片
    "AI软件": ["515230", "159819", "515400"],   # 软件 / AI / 大数据
    "机器人": ["562500", "159770"],
    "金融": ["512000", "512800"],                # 券商 / 银行
    "金属": ["512400", "518880", "516780"],      # 有色 / 黄金 / 稀土
    "新能源": ["515790", "515030", "159755"],    # 光伏 / 新能车 / 电池
    "旧能源": ["515220", "159611"],              # 煤炭 / 电力
    "内需": ["159928", "512690", "512010", "512660"],  # 消费 / 白酒 / 医药 / 军工
}


def fetch_etf_quotes(codes: list[str] | None = None) -> dict[str, dict]:
    """ETF 行情：{code: {name, price, pct, amount, turnover, vol_ratio, main_net, main_pct, super_net}}。

    失败返回 {}。
    """
    try:
        import akshare as ak

        df = ak.fund_etf_spot_em()
    except Exception as exc:
        logger.warning("ETF 行情获取失败: %s", exc)
        return {}
    if df is None or df.empty or "代码" not in df.columns:
        return {}
    want = set(codes) if codes else set(INDEX_ETFS) | {c for v in SECTOR_ETFS.values() for c in v}
    out: dict[str, dict] = {}
    try:
        df = df[df["代码"].astype(str).isin(want)]
    except Exception:
        return {}
    for _, r in df.iterrows():
        code = str(r["代码"])
        try:
            out[code] = {
                "name": str(r.get("名称", "")),
                "price": _f(r, "最新价"),
                "pct": _f(r, "涨跌幅"),
                "amount": _f(r, "成交额"),          # 元
                "turnover": _f(r, "换手率"),
                "vol_ratio": _f(r, "量比"),
                "main_net": _f(r, "主力净流入-净额"),
                "main_pct": _f(r, "主力净流入-净占比"),
                "super_net": _f(r, "超大单净流入-净额"),
            }
        except Exception as exc:
            logger.warning("ETF 行解析失败 %s: %s", code, exc)
            continue
    return out


def _f(row, col) -> float | None:
    import math

    try:
        v = row.get(col)
        if v is None:
            return None
        fv = float(v)
        return fv if not math.isnan(fv) else None
    except (TypeError, ValueError):
        return None


def etf_line(etf: dict) -> str:
    """单只 ETF 的文本行（供 LLM 输入/纯文本渲染）。"""
    if not etf:
        return ""
    parts = [etf.get("name") or ""]
    if etf.get("pct") is not None:
        parts.append(f"{etf['pct']:+.2f}%")
    if etf.get("amount"):
        parts.append(f"成交{etf['amount']/1e8:.1f}亿")
    if etf.get("turnover") is not None:
        parts.append(f"换手{etf['turnover']:.2f}%")
    if etf.get("vol_ratio") is not None:
        parts.append(f"量比{etf['vol_ratio']:.2f}")
    if etf.get("main_net"):
        parts.append(f"主力{etf['main_net']/1e8:+.2f}亿")
    if etf.get("super_net"):
        parts.append(f"超大单{etf['super_net']/1e8:+.2f}亿")
    return " ".join(parts)


def big_money_signal(etf: dict) -> str | None:
    """大资金进出信号（2026-08-22）：量比明显放大或超大单净流入显著。"""
    if not etf:
        return None
    hints = []
    vr = etf.get("vol_ratio")
    if vr is not None and vr >= 2.0:
        hints.append(f"量比{vr:.1f}明显放量")
    sn = etf.get("super_net")
    if sn is not None and abs(sn) >= 10e8:  # 超大单 ±10 亿
        hints.append(f"超大单{'流入' if sn > 0 else '流出'}{abs(sn)/1e8:.1f}亿")
    return "；".join(hints) if hints else None


def index_etf_signal_text() -> str:
    """指数 ETF 大资金信号文本（量比放大/超大单进出≈国家队/大资金动作）。"""
    try:
        quotes = fetch_etf_quotes(list(INDEX_ETFS))
    except Exception:
        return ""
    lines = []
    for code in ("510300", "510050", "510500", "512100", "159915", "588000"):
        q = quotes.get(code)
        if not q:
            continue
        sig = big_money_signal(q)
        if sig:
            lines.append(f"{etf_line(q)}（{sig}）")
    return "\n".join(lines)


def sector_etf_text() -> str:
    """全部重要板块 ETF 数据行（纯度高于板块指数，体现方向真实强度）。"""
    try:
        all_codes = [c for v in SECTOR_ETFS.values() for c in v]
        quotes = fetch_etf_quotes(all_codes)
    except Exception:
        return ""
    lines = []
    for sector, codes in SECTOR_ETFS.items():
        parts = []
        for c in codes:
            q = quotes.get(c)
            if q:
                parts.append(etf_line(q))
        if parts:
            lines.append(f"[{sector}] " + "；".join(parts))
    return "\n".join(lines)
