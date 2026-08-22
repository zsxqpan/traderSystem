"""周度复盘：执行纪律检查（看过程不看盈亏）+ 持仓卡片复评（[A]7）。"""
from __future__ import annotations

import datetime as dt
import sqlite3


def weekly_review(conn: sqlite3.Connection) -> dict:
    today = dt.date.today()
    last_monday = today - dt.timedelta(days=today.weekday())
    since = last_monday.isoformat()

    rogue = conn.execute(
        """SELECT COUNT(*) AS n FROM trade_records r
           JOIN trade_plans p ON r.plan_id = p.id
           WHERE p.status != 'active' AND date(r.created_at) >= ?""",
        (since,),
    ).fetchone()["n"]

    stop_hits = conn.execute(
        """SELECT COUNT(*) AS n FROM trade_records
           WHERE deviation_note LIKE '%止损%' AND date(created_at) >= ?""",
        (since,),
    ).fetchone()["n"]

    above = conn.execute(
        """SELECT COUNT(*) AS n FROM trade_records
           WHERE actual_vs_plan='above_range' AND date(created_at) >= ?""",
        (since,),
    ).fetchone()["n"]
    below = conn.execute(
        """SELECT COUNT(*) AS n FROM trade_records
           WHERE actual_vs_plan='below_range' AND date(created_at) >= ?""",
        (since,),
    ).fetchone()["n"]

    records_count = conn.execute(
        "SELECT COUNT(*) AS n FROM trade_records WHERE date(created_at) >= ?", (since,)
    ).fetchone()["n"]

    violations = []
    if rogue:
        violations.append(f"计划外交易 {rogue} 笔（计划非 active 仍有成交）")
    if above:
        violations.append(f"高于计划区间买入 {above} 笔")
    if below:
        violations.append(f"低于计划区间买入 {below} 笔")

    # 周期漂移（[A]6）：active 计划超周期持有
    try:
        from invest.discipline.records import drift_report
        drift = drift_report(conn, as_of=today.isoformat())
        if drift["n_drift"]:
            violations.append(f"周期漂移 {drift['n_drift']} 个计划超期未平仓")
    except Exception:
        drift = {"n_drift": 0, "drifts": []}

    # 持仓卡片复评（[A]7）：locked/review 卡片对当前价格 vs 止损/目标的健康状况
    cards_review = position_card_review(conn)

    score = max(0, 100 - rogue * 30 - above * 10 - below * 5 - drift["n_drift"] * 10)
    return {
        "period": f"{since}~{today.isoformat()}",
        "trade_records": int(records_count),
        "rogue_trades": int(rogue),
        "stop_hits": int(stop_hits),
        "above_range": int(above),
        "below_range": int(below),
        "cycle_drift": int(drift["n_drift"]),
        "cards_review": cards_review,
        "score": int(score),
        "violations": violations,
    }


def position_card_review(conn: sqlite3.Connection) -> list[dict]:
    """持仓卡片复评：locked/review 卡片对照最新收盘价与止损/目标。

    输出每张卡的 {card_id, symbol, level, cycle, status, close, stop_loss, target,
    hit_stop, near_stop, near_target, review_note}。
    - hit_stop: 收盘 <= 止损（应立即降级/作废）；
    - near_stop: 收盘距止损 <= 3%（进入警戒）；
    - near_target: 收盘距目标 <= 3%（接近兑现）。
    """
    rows = conn.execute(
        """SELECT c.* FROM cards c
           WHERE c.status IN ('locked','review')
           ORDER BY c.level, c.created_at DESC"""
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        card = dict(r)
        close = None
        try:
            row = conn.execute(
                """SELECT close FROM daily_bars WHERE symbol=?
                   ORDER BY REPLACE(date,'-','') DESC LIMIT 1""",
                (card["symbol"],),
            ).fetchone()
            close = float(row["close"]) if row else None
        except Exception:
            pass
        item = {
            "card_id": card["id"],
            "symbol": card["symbol"],
            "level": card["level"],
            "cycle": card["cycle"],
            "status": card["status"],
            "close": close,
            "stop_loss": card["stop_loss"],
            "target": card["target"],
            "hit_stop": False,
            "near_stop": False,
            "near_target": False,
            "review_note": "",
        }
        if close is not None:
            stop, target = card["stop_loss"], card["target"]
            if stop is not None and close <= float(stop):
                item["hit_stop"] = True
                item["review_note"] = "收盘已破止损位，应降级/作废"
            elif stop is not None and close <= float(stop) * 1.03:
                item["near_stop"] = True
                item["review_note"] = "收盘距止损不足 3%，进入警戒"
            elif target is not None and close >= float(target) * 0.97:
                item["near_target"] = True
                item["review_note"] = "接近目标价，可评估分批兑现"
            else:
                item["review_note"] = "正常持有区间"
        else:
            item["review_note"] = "无最新收盘价（数据缺失），复核数据后再评估"
        out.append(item)
    return out
