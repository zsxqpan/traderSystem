"""盘中异动监测：核心关注个股实时涨跌幅监控（东财盘口优先，新浪分钟线兜底）。"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from invest.db import connect


def _in_trading_window(now: dt.datetime | None = None) -> bool:
    """工作日 09:35-11:30 / 13:05-14:55。"""
    now = now or dt.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (dt.time(9, 35) <= t <= dt.time(11, 30)) or (dt.time(13, 5) <= t <= dt.time(14, 55))


def fetch_current_price(symbol: str) -> float | None:
    """取最新价：优先东财实时盘口（"最新"），失败回退新浪 60 分钟线最后一根收盘。"""
    try:
        import akshare as ak
        from invest.data.sources.akshare_source import call_with_timeout
        df = call_with_timeout(ak.stock_bid_ask_em, symbol=symbol, timeout=20)
        if df is not None and not df.empty and {"item", "value"} <= set(df.columns):
            row = df[df["item"] == "最新"]
            if not row.empty:
                v = pd.to_numeric(row["value"].iloc[0], errors="coerce")
                if pd.notna(v) and float(v) > 0:
                    return float(v)
    except Exception:  # noqa: BLE001
        pass
    try:
        import akshare as ak
        from invest.data.sources.akshare_source import _sina_stock_symbol
        from invest.data.sources.akshare_source import call_with_timeout
        df = call_with_timeout(ak.stock_zh_a_minute, symbol=_sina_stock_symbol(symbol), period="60", timeout=20)
        if df is None or df.empty:
            return None
        close_col = "close" if "close" in df.columns else ("收盘" if "收盘" in df.columns else None)
        if close_col is None:
            return None
        return float(df[close_col].dropna().iloc[-1])
    except Exception:  # noqa: BLE001
        return None


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


def check_core_moves(db_path: str, threshold: float = 0.03) -> list[dict]:
    """核心关注个股盘中异动检测。"""
    conn = connect(db_path)
    try:
        core = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level='core' AND out_date IS NULL"
        )]
    finally:
        conn.close()
    baselines = _baselines(db_path)
    alerts = []
    for sym in core:
        price = fetch_current_price(sym)
        base = baselines.get(sym)
        if price is None or base is None or base <= 0:
            continue
        pct = price / base - 1
        if abs(pct) >= threshold:
            alerts.append({"symbol": sym, "price": round(price, 2), "pct": round(pct, 4)})
    return alerts


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
    except Exception:  # noqa: BLE001
        return ""


def send_alerts(db_path: str, alerts: list[dict], attribute: bool = True) -> int:
    """推送异动（同标的 5 分钟限频）；attribute 时为首条异动附 LLM 归因。"""
    from invest.notifier import Notifier
    notifier = Notifier()
    sent = 0
    for i, a in enumerate(alerts):
        msg = f"【盘中异动】{a['symbol']} 现价 {a['price']:.2f}，较昨收 {a['pct']:+.2%}"
        if attribute and i == 0:
            try:
                note = _attribute(db_path, a)
            except Exception:  # noqa: BLE001
                note = ""
            if note:
                msg += f"\n归因: {note[:100]}"
        ok = notifier.send_text(
            msg,
            key=f"intraday_{a['symbol']}",
            min_interval=300,
        )
        sent += int(ok)
    return sent