"""盘中异动监测：核心关注个股实时涨跌幅监控（三源直连轮询）。

实时行情通道（2026-08-15 决策）：新浪 hq.sinajs.cn / 腾讯 qt.gtimg.cn /
东财 push2 三源直连 Level-1 快照轮询，批量取核心池，3-5 秒间隔；
任一源失败自动切换下一源；行情时间戳与接收时刻差值超阈值视为
不新鲜（stale），不支撑决策（数据失效即防守）。

推送时效分级（2026-08-21 起统一每标的 30 分钟限频）：
- P0（core 核心关注异动）：交易时段内推送，**1800s（30 分钟）限频**；
- P1（track 跟踪异动）：1800s（30 分钟）限频（避免噪音）；
- P2（rest/其他）：不实时推送（仅晚间复盘汇总）；
- 限频按标的独立（key=intraday_{symbol}）：同一只票通知一次后 30 分钟内不再通知；
- 非交易时段：盘中异动类一律静默（防御，正常调度已由 _in_trading_window 守护）。
"""
from __future__ import annotations

import datetime as dt
import time

from invest.db import connect


def _in_trading_window(now: dt.datetime | None = None) -> bool:
    """工作日 09:35-11:30 / 13:05-14:55。"""
    now = now or dt.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (dt.time(9, 35) <= t <= dt.time(11, 30)) or (dt.time(13, 5) <= t <= dt.time(14, 55))


def _bare_symbol(sym: str) -> str:
    """sz000001 -> 000001；600519 -> 600519（裸代码，与 candidate_pool 一致）。"""
    s = sym.lower()
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix):
            return s[2:]
    return s


def fetch_current_price(symbol: str) -> float | None:
    """取单只最新价：三源直连轮询，失败返回 None（保持旧接口签名）。"""
    from invest.data.realtime import RealtimeQuoter, _to_market_symbol
    try:
        with RealtimeQuoter() as q:
            quotes = q.fetch([symbol])
        if not quotes:
            return None
        msym = _to_market_symbol(symbol)
        qq = quotes.get(msym) or next(iter(quotes.values()))
        return qq.price if qq else None
    except Exception:
        return None


def fetch_batch_prices(
    symbols: list[str],
    max_lag: float = 10.0,
    db_path: str | None = None,
) -> dict[str, float]:
    """批量取最新价（核心池一次轮询）：返回 {裸代码: price}，只收新鲜数据。

    db_path 提供时，将源/延迟/stale 计数写入 job_runs(job='realtime') 留痕。

    2026-08-26：非交易时段（收盘后/休市）放宽新鲜度——三源返回的时间戳是
    最近一次行情时刻（收盘后即收盘时刻），按 10s 严格过滤会把收盘价全部丢弃，
    导致盘中报告「核心关注」表格空白。非交易时段接受最近 12 小时内的行情
    （收盘后=收盘价，即最新可得）。
    """
    from invest.data.realtime import RealtimeQuoter, is_fresh, log_realtime_health
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        return {}
    try:
        with RealtimeQuoter() as q:
            quotes = q.fetch(symbols)
    except Exception:
        return {}
    if db_path:
        log_realtime_health(db_path, quotes, q.source_failures)
    # 交易时段严格 10s；非交易时段放宽到 12h（收盘价/上一交易时段行情即最新）
    effective_lag = max_lag if _in_trading_window() else 12 * 3600.0
    out: dict[str, float] = {}
    for sym, qq in quotes.items():
        if qq.price is None or not is_fresh(qq, max_lag=effective_lag):
            continue
        out[_bare_symbol(sym)] = qq.price
    return out


def _baselines(db_path: str) -> dict[str, float]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """SELECT d.symbol, d.close FROM daily_bars d
               JOIN (SELECT symbol, MAX(date) AS md FROM daily_bars GROUP BY symbol) m
                 ON d.symbol=m.symbol AND d.date=m.md"""
        ).fetchall()
        return {r["symbol"]: float(r["close"]) for r in rows}
    finally:
        conn.close()


def _move_threshold(symbol: str) -> float:
    """异动阈值（2026-08-18 按板块区分）：主板 ±3%；创业板(300/301)/科创板(688/689) ±6%。"""
    s = (symbol or "").split(".")[0]
    if s.startswith(("300", "301", "688", "689")):
        return 0.06
    return 0.03


def check_core_moves(db_path: str, threshold: float | None = None) -> list[dict]:
    """核心关注个股盘中异动检测：批量取价一次轮询，缺失标的跳过。

    数据失效即防守：三源全挂或行情不新鲜时返回空列表（不推送、不抛错）；
    轮询延迟/源切换写入 job_runs(job='realtime') 留痕。

    阈值（2026-08-18 按板块区分）：threshold=None 时主板 ±3%、创业板/科创板 ±6%
    （原统一 ±5% 噪音过多）；显式传 threshold 则全标的用该值（兼容调用方）。
    """
    conn = connect(db_path)
    try:
        core = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level='core' AND out_date IS NULL"
        )]
    finally:
        conn.close()
    if not core:
        return []
    baselines = _baselines(db_path)
    prices = fetch_batch_prices(core, db_path=db_path)
    alerts = []
    for sym in core:
        price = prices.get(sym)
        base = baselines.get(sym)
        if price is None or base is None or base <= 0:
            continue
        pct = price / base - 1
        thr = threshold if threshold is not None else _move_threshold(sym)
        if abs(pct) >= thr:
            alerts.append({
                "symbol": sym, "price": round(price, 2), "pct": round(pct, 4),
                "threshold": thr,
            })
    return alerts


_attr_limited: dict[str, float] = {}  # 归因限频状态（2026-08-21）


def _attribute_ok(key: str, interval: float) -> bool:
    """归因限频（2026-08-21）：同一标的关键期内只做一次 LLM 归因。"""
    now = time.time()
    if now - _attr_limited.get(key, 0.0) < interval:
        return False
    _attr_limited[key] = now
    return True


def _attribute(db_path: str, alert: dict) -> str:
    """预算内的 LLM 归因（job=intraday，受每日 token 预算约束）。失败返回空串。"""
    try:
        from invest.agent.agents import run_trade
        conn = connect(db_path)
        try:
            return run_trade(
                conn,
                f"解释 {alert['symbol']} 今日盘中异动 {alert['pct']:+.2%} 的可能原因，"
                "结合行业强度/资金属性/联动数据，一句话归因（不超过50字）",
                job="intraday",
            )
        finally:
            conn.close()
    except Exception:
        return ""


# 推送时效分级：候选池 level -> (优先级, 限频秒)
# 2026-08-21 统一每标的 30 分钟限频（core 原 180s 上调；track 1800s 不变）
_PUSH_POLICY = {
    "core": ("P0", 1800),   # 核心关注：交易时段推，1800s(30分钟) 限频
    "track": ("P1", 1800),  # 跟踪：降频，1800s 限频
    "rest": ("P2", 0),      # 其余：不实时推（仅晚间汇总）
}


def send_alerts(db_path: str, alerts: list[dict], attribute: bool = True) -> int:
    """推送异动，按候选池等级时效分级（v3 14.3）。

    - 非交易时段：一律静默返回 0（防御：正常调度已由 _in_trading_window 守护）；
    - P0（core）/P1（track）：交易时段推送，统一 **1800s（30 分钟）限频**（按标的独立）；
    - P2（rest）：不实时推送，仅晚间复盘汇总（这里跳过）；
    - attribute 时为首条 P0 异动附 LLM 归因（与发送同限频，30 分钟内每标的最多归因一次）。
    """
    if not _in_trading_window():
        return 0
    from invest.notifier import Notifier
    conn = connect(db_path)
    try:
        levels = {
            r["symbol"]: r["level"]
            for r in conn.execute("SELECT symbol, level FROM candidate_pool WHERE out_date IS NULL")
        }
    finally:
        conn.close()
    notifier = Notifier()
    sent = 0
    # 只对 P0/P1 推送；P2 静默（晚间复盘统一汇总）
    pushable = [a for a in alerts if levels.get(a["symbol"], "rest") in ("core", "track")]
    if not pushable:
        return 0
    for i, a in enumerate(pushable):
        level = levels.get(a["symbol"], "rest")
        priority, interval = _PUSH_POLICY.get(level, ("P2", 0))
        tag = f"[{priority}]"
        thr = a.get("threshold")
        thr_txt = f"，异动>{thr:.0%}" if thr else ""
        msg = f"{tag}【盘中异动】{a['symbol']} 现价 {a['price']:.2f}，较昨收 {a['pct']:+.2%}{thr_txt}"
        attr_key = f"intraday_{a['symbol']}"
        # 2026-08-21 修复：LLM 归因也必须与发送同限频——此前限频只拦发送不拦归因，
        # 盘中异动频繁时每 10s tick 都跑一次归因（单日爆掉 200 万+ token）
        if attribute and i == 0 and priority == "P0" and _attribute_ok(attr_key, interval):
            try:
                note = _attribute(db_path, a)
            except Exception:
                note = ""
            if note:
                msg += f"\n归因: {note[:100]}"
        ok = notifier.send_text(
            msg,
            key=attr_key,
            min_interval=interval,
        )
        sent += int(ok)
    return sent
