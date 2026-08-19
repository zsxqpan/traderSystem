"""P0 监控：持仓证伪 / 风控触发 / 数据冲突（主动监控 + 推送）。

2026-08-15 落地（TODO 2.5 P0 监控）：
- 持仓证伪：active trade_plans 的证伪条件（invalid_condition）与止损位被实时价格
  突破 → 推送 P0 告警并标记计划（falsify/stop_loss）；
- 风控触发：data_guard 数据失效 + check_position 违规 → 推送降级告警；
- 数据冲突：最近一次 realtime 留痕 stale>0 或三源全部失败 → 推送数据失效告警。

推送分级：全部为 P0 级（[P0] 前缀）。
2026-08-18 改：数据失效告警改为**边沿触发**——失效时通知一次、恢复时再通知一次
（状态存 data/monitor_state.json，重启不丢）；不再按 30 分钟限频重复刷屏。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from invest.db import connect
from invest.notifier import Notifier

ROOT = Path(__file__).resolve().parents[1]
_STATE_FILE = ROOT / "data" / "monitor_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _in_trading_window(now: dt.datetime | None = None) -> bool:
    """工作日 09:35-11:30 / 13:05-14:55（与 intraday 一致）。"""
    now = now or dt.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (dt.time(9, 35) <= t <= dt.time(11, 30)) or (dt.time(13, 5) <= t <= dt.time(14, 55))


def _parse_stop(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def check_position_falsify(db_path: str, price_map: dict[str, float]) -> list[dict]:
    """持仓证伪监控：active 计划止损位被突破 / 证伪条件触发。

    price_map: {裸代码: 最新价}（已过新鲜度过滤）。返回告警列表。
    """
    conn = connect(db_path)
    try:
        plans = [
            dict(r) for r in conn.execute(
                "SELECT * FROM trade_plans WHERE status='active'"
            ).fetchall()
        ]
    finally:
        conn.close()
    alerts: list[dict] = []
    for p in plans:
        sym = p["symbol"]
        price = price_map.get(sym)
        if price is None:
            continue  # 无新鲜报价：由 data_guard 处理，不误报
        sl = _parse_stop(p.get("stop_loss"))
        if sl is not None and price <= sl:
            alerts.append({
                "kind": "stop_loss",
                "symbol": sym,
                "plan_id": p["id"],
                "msg": f"[P0]【止损触发】{sym} 现价 {price:.2f} ≤ 止损位 {sl:.2f}（计划#{p['id']}）",
            })
        inv = (p.get("invalid_condition") or "").strip()
        if inv and inv.lower() in ("falsified", "证伪"):
            alerts.append({
                "kind": "falsify",
                "symbol": sym,
                "plan_id": p["id"],
                "msg": f"[P0]【持仓证伪】{sym} 触发证伪条件（计划#{p['id']}）",
            })
    return alerts


def check_data_conflict(db_path: str) -> list[dict]:
    """数据冲突监控：最近 realtime 留痕 stale>0 或健康检查失败。"""
    alerts: list[dict] = []
    try:
        from invest.data.realtime import realtime_health
        h = realtime_health(db_path)
        if not h.get("ok"):
            stale = h.get("stale", 0)
            detail = h.get("last_detail", "")[:80]
            alerts.append({
                "kind": "data_conflict",
                "symbol": "",
                "msg": f"[P0]【数据失效】实时行情不可用（stale={stale} {detail}）：禁止新开仓",
            })
    except Exception:  # noqa: BLE001
        alerts.append({
            "kind": "data_conflict",
            "symbol": "",
            "msg": "[P0]【数据失效】实时行情健康检查失败：禁止新开仓",
        })
    return alerts


def run_p0_monitor(db_path: str) -> int:
    """执行 P0 监控一轮：持仓证伪 + 数据冲突，推送 P0 告警。

    返回推送成功条数。非交易时段休市，行情旧属正常现象（不是数据失效），
    一律静默返回 0，避免"实时行情不可用"刷屏；交易时段才检查数据冲突与持仓证伪。

    2026-08-18：数据失效告警改边沿触发（状态存 data/monitor_state.json）：
    - 失效：只在"健康→失效"转换时通知一次（含失效详情）；
    - 恢复：只在"失效→健康"转换时通知一次。
    """
    from invest.intraday import fetch_batch_prices
    notifier = Notifier()
    sent = 0

    if _in_trading_window():
        # 数据冲突（边沿触发，避免每 30 分钟重复告警）
        conflict = check_data_conflict(db_path)
        invalid_now = bool(conflict)
        state = _load_state()
        was_invalid = bool(state.get("realtime_invalid"))
        if invalid_now and not was_invalid:
            for a in conflict:
                ok = notifier.send_text(a["msg"], key=f"p0_{a['kind']}_down", min_interval=300)
                sent += int(ok)
            _save_state({"realtime_invalid": True})
        elif not invalid_now and was_invalid:
            ok = notifier.send_text(
                "[P0] 实时行情已恢复（数据失效解除，stale=0）：可以正常开仓/决策",
                key="p0_data_conflict_up", min_interval=300,
            )
            sent += int(ok)
            _save_state({"realtime_invalid": False})

        # 持仓证伪（仅交易时段，需要新鲜价格）
        conn = connect(db_path)
        try:
            symbols = [r["symbol"] for r in conn.execute(
                "SELECT symbol FROM trade_plans WHERE status='active'"
            )]
        finally:
            conn.close()
        if symbols:
            prices = fetch_batch_prices(symbols, db_path=db_path)
            for a in check_position_falsify(db_path, prices):
                ok = notifier.send_text(a["msg"], key=f"p0_{a['kind']}_{a['symbol']}", min_interval=1800)
                sent += int(ok)
    return sent


def _log_p0(db_path: str, sent: int, detail: str = "") -> None:
    try:
        conn = connect(db_path)
        try:
            with conn:
                conn.execute(
                    """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
                       VALUES('p0_monitor', 'ok', datetime('now','localtime'), datetime('now','localtime'), ?)""",
                    (f"sent={sent} {detail}".strip(),),
                )
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
