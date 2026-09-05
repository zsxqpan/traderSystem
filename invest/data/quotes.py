"""统一实时行情契约：AssetRef / QuoteResult + 规范化 + 覆盖率。

消费方（盘中/竞价报告、query_realtime_quote、intraday）只走本模块，
不再各自拼三源/指数/ETF。网络请求由底层源负责（均 trust_env=False）。
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 8 大指数：裸代码 → (市场前缀代码, 名称)
INDEX_META: dict[str, tuple[str, str]] = {
    "000001": ("sh000001", "上证指数"),
    "399001": ("sz399001", "深证成指"),
    "000300": ("sh000300", "沪深300"),
    "000905": ("sh000905", "中证500"),
    "000852": ("sh000852", "中证1000"),
    "000688": ("sh000688", "科创50"),
    "399006": ("sz399006", "创业板指"),
    "899050": ("bj899050", "北证50"),
}
INDEX_UNIVERSE: tuple[str, ...] = tuple(INDEX_META)

# 报告关键标的最低覆盖率（live+有价回退）；未达标则降级为事实列表
KEY_COVERAGE_MIN = 0.6
_LIVE_LAG = 10.0


@dataclass(frozen=True)
class AssetRef:
    """规范化标的：裸 6 位代码 + 对象类型。"""

    symbol: str
    obj_type: str = "stock"
    name: str = ""


@dataclass
class QuoteResult:
    """统一报价结果：每个请求标的恰好一条，status 必为 live/fallback/missing。"""

    ref: AssetRef
    price: float | None = None
    prev_close: float | None = None
    pct: float | None = None
    ts: dt.datetime | None = None
    src: str = ""
    freshness: str = "unknown"  # live / stale / unknown
    fallback_level: str = "none"  # none / last_close / suspended / no_history / source_fail
    missing_reason: str = ""
    status: str = "missing"  # live / fallback / missing
    extras: dict | None = None  # ETF 成交额/量比/资金等，个股/指数可空


def normalize_symbol(symbol: str, obj_type: str = "stock") -> str:
    """归一为裸 6 位数字代码；非法返回空串。"""
    s = (symbol or "").strip()
    if not s:
        return ""
    sl = s.lower()
    for prefix in ("sh", "sz", "bj"):
        if sl.startswith(prefix) and len(sl) > 2:
            s = sl[2:]
            break
    s = s.upper().split(".")[0]
    s = "".join(ch for ch in s if ch.isdigit())
    return s if len(s) == 6 else ""


def parse_asset(symbol: str, obj_type: str = "stock", name: str = "") -> AssetRef | None:
    """解析并规范化；非法返回 None。"""
    if obj_type not in ("stock", "index", "etf"):
        return None
    code = normalize_symbol(symbol, obj_type)
    if not code:
        return None
    return AssetRef(symbol=code, obj_type=obj_type, name=name or resolve_name(code, obj_type))


def market_symbol(symbol: str, obj_type: str = "stock") -> str:
    """裸代码 → 市场前缀（sh/sz/bj + 6 位）。非法返回空串。"""
    code = normalize_symbol(symbol, obj_type)
    if not code:
        return ""
    if obj_type == "index":
        meta = INDEX_META.get(code)
        if meta:
            return meta[0]
        if code.startswith("399"):
            return "sz" + code
        if code.startswith(("4", "8")):
            return "bj" + code
        return "sh" + code
    if obj_type == "etf":
        return ("sz" if code.startswith(("1", "0")) else "sh") + code
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    if code.startswith("9"):
        return "sh" + code
    return "sz" + code


def resolve_name(symbol: str, obj_type: str = "stock") -> str:
    """已知指数/ETF 名称；个股默认空（由行情源补）。"""
    code = normalize_symbol(symbol, obj_type)
    if not code:
        return ""
    if obj_type == "index":
        meta = INDEX_META.get(code)
        return meta[1] if meta else ""
    if obj_type == "etf":
        try:
            from invest.data.etf import INDEX_ETFS

            return INDEX_ETFS.get(code, "")
        except Exception:
            return ""
    return ""


def status_label(result: QuoteResult) -> str:
    """报告展示：实时 / 过期实时 / 最近收盘 / 停牌 / 缺历史 / 源失败。"""
    if result.fallback_level == "suspended" or "停牌" in (result.missing_reason or ""):
        return "停牌"
    if result.freshness == "stale":
        return "过期实时"
    if result.status == "live":
        return "实时"
    if result.fallback_level == "last_close":
        return "最近收盘"
    if result.fallback_level == "no_history":
        return "缺历史"
    if result.fallback_level == "source_fail":
        return "源失败"
    if result.status == "fallback":
        if result.fallback_level == "none" and result.freshness in ("stale", "unknown"):
            return "过期实时"
        return "最近收盘"
    if result.status == "missing":
        return "缺历史" if result.fallback_level == "no_history" else "源失败"
    return "源失败"


def summarize_coverage(
    results: list[QuoteResult], min_ratio: float = KEY_COVERAGE_MIN
) -> dict:
    n = len(results)
    live = sum(1 for r in results if r.status == "live")
    fallback = sum(1 for r in results if r.status == "fallback")
    missing = sum(1 for r in results if r.status == "missing")
    usable = sum(1 for r in results if r.status in ("live", "fallback") and r.price)
    cov = (usable / n) if n else 1.0
    return {
        "requested": n,
        "live": live,
        "fallback": fallback,
        "missing": missing,
        "coverage": cov,
        "ok": cov >= min_ratio,
        "label": f"{usable}/{n}",
    }


def report_should_degrade(
    index_results: list[QuoteResult],
    stock_results: list[QuoteResult],
    min_ratio: float = KEY_COVERAGE_MIN,
) -> tuple[bool, dict]:
    """关键标的覆盖不足 → 降级。指数至少 1 只 live；个股覆盖率 ≥ 门槛。"""
    idx_live = sum(1 for r in index_results if r.status == "live")
    stock = summarize_coverage(stock_results, min_ratio)
    idx_ok = (not index_results) or idx_live >= 1
    ok = idx_ok and stock["ok"]
    return (not ok), {
        "index_live": idx_live,
        "index_ok": idx_ok,
        "index_requested": bool(index_results),
        "stock": stock,
        "ok": ok,
    }


def degrade_alert_text(cov_info: dict) -> str:
    """降级告警：写清是指数 0 live 还是个股覆盖不足（指数全挂不打印个股 100%）。"""
    parts: list[str] = []
    if cov_info.get("index_requested") and not cov_info.get("index_ok"):
        parts.append(f"指数 {cov_info.get('index_live', 0)} live")
    stock = cov_info.get("stock") or {}
    if not stock.get("ok", True):
        parts.append(
            f"个股覆盖不足 {stock.get('label', '?')}（{stock.get('coverage', 0):.0%}）"
        )
    reason = "；".join(parts) or "关键标的覆盖不足"
    return f"【告警】{reason}，已降级为事实列表，未生成主线结论"


def coverage_text(
    index_results: list[QuoteResult],
    stock_results: list[QuoteResult],
) -> str:
    c = summarize_coverage(list(index_results) + list(stock_results))
    return (
        f"覆盖率 {c['label']}（{c['coverage']:.0%}）"
        f" 实时{c['live']} 回退{c['fallback']} 缺失{c['missing']}"
    )


def _bare(sym: str) -> str:
    s = (sym or "").lower()
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix) and len(s) > 2:
            return s[2:]
    return s


def _last_closes(symbols: list[str], db_path: str | None, conn) -> dict[str, float]:
    if not symbols:
        return {}
    own = False
    if conn is None:
        if not db_path:
            return {}
        from invest.db import connect

        conn = connect(db_path)
        own = True
    try:
        marks = ",".join("?" * len(symbols))
        rows = conn.execute(
            f"""SELECT d.symbol, d.close FROM daily_bars d
                JOIN (SELECT symbol, MAX(REPLACE(date,'-','')) md FROM daily_bars
                      WHERE symbol IN ({marks}) GROUP BY symbol) m
                  ON d.symbol=m.symbol AND REPLACE(d.date,'-','')=m.md""",
            symbols,
        ).fetchall()
        return {str(r["symbol"]): float(r["close"]) for r in rows if r["close"]}
    except Exception:
        return {}
    finally:
        if own:
            conn.close()


def _freshness_of(quote, now: dt.datetime, trading: bool) -> str:
    from invest.data.realtime import is_fresh

    if quote is None or getattr(quote, "ts", None) is None:
        return "unknown"
    # 行情时钟略快于本机时 lag 为负，仍视为 live
    lag = (now - quote.ts).total_seconds()
    if -2.0 <= lag < 0:
        return "live"
    if trading:
        return "live" if is_fresh(quote, now=now, max_lag=_LIVE_LAG) else "stale"
    return "live" if abs(lag) <= 12 * 3600 else "stale"


def _classify_stock(
    ref: AssetRef,
    quote,
    last_close: float | None,
    last_err: Exception | None,
    now: dt.datetime,
    trading: bool,
) -> QuoteResult:
    name = ref.name or getattr(quote, "name", "") or ""
    ref = AssetRef(ref.symbol, ref.obj_type, name)
    if quote is not None and getattr(quote, "suspended", False) is True:
        price = getattr(quote, "prev_close", None) or last_close
        return QuoteResult(
            ref=ref, price=price, prev_close=getattr(quote, "prev_close", None) or last_close,
            ts=quote.ts, src=quote.src, freshness="unknown",
            fallback_level="suspended", missing_reason="停牌",
            status="fallback" if price else "missing",
        )
    if quote is not None and quote.price and quote.price > 0:
        fresh = _freshness_of(quote, now, trading)
        prev = getattr(quote, "prev_close", None) or last_close
        pct = quote.pct
        if pct is None and prev and prev > 0:
            pct = quote.price / prev - 1.0
        live_ok = fresh == "live"
        return QuoteResult(
            ref=ref, price=quote.price, prev_close=prev, pct=pct,
            ts=quote.ts, src=quote.src, freshness=fresh,
            fallback_level="none",
            status="live" if live_ok else "fallback",
        )
    if last_close:
        return QuoteResult(
            ref=ref, price=last_close, prev_close=last_close, src="daily_bars",
            freshness="unknown", fallback_level="last_close",
            missing_reason="回退最近收盘", status="fallback",
        )
    if last_err is not None:
        return QuoteResult(
            ref=ref, status="missing", fallback_level="source_fail",
            missing_reason="源失败",
        )
    return QuoteResult(
        ref=ref, status="missing", fallback_level="no_history",
        missing_reason="缺历史",
    )


def _pull_stock_quotes(quoter, symbols: list[str]) -> tuple[dict, Exception | None]:
    """优先 _fetch_merged（保留停牌）；返回值不是 (dict, err) 时回退 fetch。"""
    merged_fn = getattr(quoter, "_fetch_merged", None)
    if callable(merged_fn):
        try:
            out = merged_fn(symbols)
            if isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], dict):
                return out[0], out[1]
        except Exception as exc:
            return {}, exc
    try:
        return (quoter.fetch(symbols) or {}), None
    except Exception as exc:
        return {}, exc


def _quotes_stock(
    refs: list[AssetRef],
    now: dt.datetime,
    db_path: str | None,
    conn,
    quoter,
) -> list[QuoteResult]:
    from invest.data.realtime import RealtimeQuoter
    from invest.intraday import _in_trading_window

    trading = _in_trading_window(now)
    valid = [r.symbol for r in refs if r.symbol]
    raw: dict = {}
    last_err: Exception | None = None
    if valid:
        try:
            if quoter is not None:
                raw, last_err = _pull_stock_quotes(quoter, valid)
            else:
                with RealtimeQuoter() as qrt:
                    raw, last_err = _pull_stock_quotes(qrt, valid)
        except Exception as exc:
            last_err = exc
            raw = {}
    by_bare: dict = {}
    for key, qq in (raw or {}).items():
        qsym = getattr(qq, "symbol", None)
        by_bare[_bare(qsym if isinstance(qsym, str) and qsym else key)] = qq
    closes = _last_closes([r.symbol for r in refs], db_path, conn)
    return [
        _classify_stock(ref, by_bare.get(ref.symbol), closes.get(ref.symbol), last_err, now, trading)
        for ref in refs
    ]


def _prev_from_pct(price: float, pct: float | None) -> float | None:
    """由现价与涨跌幅（小数）反推昨收；无法计算则 None。"""
    if pct is None:
        return None
    denom = 1.0 + float(pct)
    if denom == 0:
        return None
    try:
        return float(price) / denom
    except (TypeError, ValueError):
        return None


def _quotes_index(refs: list[AssetRef], now: dt.datetime) -> list[QuoteResult]:
    from invest.data.index_realtime import fetch_index_realtime

    _ = now  # 不用本机 now 冒充行情钟
    try:
        idx = fetch_index_realtime()
    except Exception as exc:
        logger.warning("指数实时失败: %s", exc)
        idx = {}
    out: list[QuoteResult] = []
    for ref in refs:
        d = idx.get(ref.symbol) if idx else None
        if d and d.get("price"):
            pct = d.get("pct")
            if pct is not None:
                pct = float(pct) / 100.0  # index_realtime 返回百分数（0.35=+0.35%）
            price = float(d["price"])
            prev = d.get("prev_close")
            if prev is None:
                prev = _prev_from_pct(price, pct)
            out.append(QuoteResult(
                ref=AssetRef(ref.symbol, "index", d.get("name") or ref.name),
                price=price, prev_close=prev, pct=pct, src="tencent",
                freshness="unknown", fallback_level="none", status="live", ts=None,
            ))
        else:
            out.append(QuoteResult(
                ref=ref, status="missing", fallback_level="source_fail",
                missing_reason="源失败",
            ))
    return out


def _quotes_etf(refs: list[AssetRef], now: dt.datetime) -> list[QuoteResult]:
    from invest.data.etf import fetch_etf_quotes

    _ = now  # 不用本机 now 冒充行情钟
    codes = [r.symbol for r in refs]
    try:
        data = fetch_etf_quotes(codes)
    except Exception as exc:
        logger.warning("ETF 行情失败: %s", exc)
        data = {}
    out: list[QuoteResult] = []
    for ref in refs:
        d = data.get(ref.symbol) if data else None
        if d and d.get("price"):
            pct = d.get("pct")
            if pct is not None:
                pct = float(pct) / 100.0
            price = float(d["price"])
            prev = d.get("prev_close")
            if prev is None:
                prev = _prev_from_pct(price, pct)
            out.append(QuoteResult(
                ref=AssetRef(ref.symbol, "etf", d.get("name") or ref.name),
                price=price, prev_close=prev, pct=pct, src="eastmoney",
                freshness="unknown", fallback_level="none", status="live", ts=None,
                extras={
                    "amount": d.get("amount"),
                    "vol_ratio": d.get("vol_ratio"),
                    "main_net": d.get("main_net"),
                    "super_net": d.get("super_net"),
                },
            ))
        else:
            out.append(QuoteResult(
                ref=ref, status="missing", fallback_level="source_fail",
                missing_reason="源失败",
            ))
    return out


def get_quotes(
    symbols: list[str],
    obj_type: str = "stock",
    *,
    db_path: str | None = None,
    conn=None,
    quoter=None,
    now: dt.datetime | None = None,
) -> list[QuoteResult]:
    """按请求顺序返回恰好一条 QuoteResult / 标的，不静默丢行。"""
    now = now or dt.datetime.now()
    refs: list[AssetRef] = []
    for s in symbols:
        ref = parse_asset(s, obj_type)
        if ref is None:
            refs.append(AssetRef(symbol=(s or "").strip(), obj_type=obj_type))
        else:
            refs.append(ref)
    if obj_type == "index":
        return _quotes_index(refs, now)
    if obj_type == "etf":
        return _quotes_etf(refs, now)
    return _quotes_stock(refs, now, db_path, conn, quoter)


def probe_realtime(symbols: list[str] | None = None, quoter=None) -> dict:
    """交易时段实时能力探测：看本次逐标的 live，不读 job_runs.realtime。"""
    from invest.data.realtime import RealtimeQuoter

    targets = list(symbols or ["600519"])
    own = False
    if quoter is None:
        quoter = RealtimeQuoter(retries=0, timeout=2.0)
        own = True
    try:
        results = get_quotes(targets, obj_type="stock", quoter=quoter)
    except Exception as exc:
        return {"ok": False, "live": 0, "requested": len(targets), "detail": str(exc)}
    finally:
        if own:
            try:
                quoter.close()
            except Exception:
                pass
    live = sum(1 for r in results if r.status == "live" and r.freshness == "live")
    return {
        "ok": live > 0,
        "live": live,
        "requested": len(results),
        "detail": f"probe live={live}/{len(results)}",
    }
