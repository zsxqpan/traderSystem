"""风控规则引擎：评级仓位上限、单票/行业集中度、止损触发、回撤阈值、数据失效降级。

自动化降级规则（2026-08-15，对齐 v3 4.3 / TODO 2.5）：
- 数据失效（实时行情 ok=False 或日线陈旧）→ 禁止新开仓（硬约束）；
- 证伪监控失效（标的无实时报价）→ 该标的标记流动性违约，冻结新开仓；
- 组合穿透（行业/风险簇超限）→ 相关对象冻结新开仓。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from invest.config import load_yaml_config

from .rating import get_position_limit


@dataclass
class RiskRules:
    max_drawdown: float = 0.15
    single_position: float = 0.15
    industry_limit: float = 0.30
    cash_floor: float = 0.20


def load_risk_rules(conn: sqlite3.Connection | None = None, config: dict | None = None) -> RiskRules:
    config = config or load_yaml_config()
    limits = config.get("limits", {})
    rules = RiskRules(
        max_drawdown=float(limits.get("max_drawdown", 0.15)),
        single_position=float(limits.get("single_position", 0.15)),
        industry_limit=float(limits.get("industry_limit", 0.30)),
        cash_floor=float(limits.get("cash_floor", 0.20)),
    )
    if conn is not None:
        for row in conn.execute("SELECT rule_type, params_json FROM risk_rules WHERE enabled=1"):
            try:
                import json
                params = json.loads(row["params_json"] or "{}")
            except (ValueError, TypeError):
                params = {}
            key = row["rule_type"]
            if key == "max_drawdown" and "value" in params:
                rules.max_drawdown = float(params["value"])
            elif key == "single_position" and "value" in params:
                rules.single_position = float(params["value"])
            elif key == "industry_limit" and "value" in params:
                rules.industry_limit = float(params["value"])
            elif key == "cash_floor" and "value" in params:
                rules.cash_floor = float(params["value"])
    return rules


def data_guard(conn: sqlite3.Connection, db_path: str = "") -> list[str]:
    """数据失效降级检查：返回违规列表（空=数据可用）。

    - 实时行情健康：realtime_health() 查询 job_runs(job='realtime') 最近留痕；
    - 日线新鲜度：daily_bars 最新日期距今天交易日不应过旧（>7 自然日视为失效）。
    """
    violations: list[str] = []
    if not db_path:
        try:
            from invest.config import get_settings
            db_path = get_settings().db_path
        except Exception:  # noqa: BLE001
            db_path = ""
    try:
        from invest.data.realtime import realtime_health
        h = realtime_health(db_path) if db_path else {"ok": False}
    except Exception:  # noqa: BLE001
        h = {"ok": False}
    if not h.get("ok"):
        stale = h.get("stale", 0)
        violations.append(f"实时行情失效（stale={stale}）：禁止新开仓，等待行情恢复")
    try:
        row = conn.execute("SELECT MAX(date) d FROM daily_bars").fetchone()
        if row and row["d"]:
            import datetime as _dt
            latest = _dt.date.fromisoformat(str(row["d"]))
            age = (_dt.date.today() - latest).days
            if age > 7:
                violations.append(f"日线数据陈旧（最新 {row['d']}，距今 {age} 天）：禁止新开仓")
    except Exception:  # noqa: BLE001
        pass
    return violations



def check_position(
    conn: sqlite3.Connection,
    proposed: float,
    total_position: float = 0.0,
    industry_position: float = 0.0,
    rules: RiskRules | None = None,
    data_ok: bool = True,
    symbol: str = "",
) -> list[str]:
    """返回违规列表（空=通过）。

    data_ok=False 表示实时行情/日线数据失效，强制禁止新开仓（自动化降级）。
    """
    rules = rules or load_risk_rules(conn)
    violations = []
    if not data_ok:
        violations.append("数据失效：禁止新开仓（自动化降级，v3 4.3）")
    try:
        from .costs import is_frozen
        if symbol and is_frozen(conn, symbol):
            violations.append(f"{symbol} 流动性冻结：禁止新开仓（止损无法成交闭环）")
    except Exception:  # noqa: BLE001
        pass
    cap = get_position_limit(conn)
    if total_position + proposed > cap:
        violations.append(f"总仓位 {total_position + proposed:.0%} 超过评级上限 {cap:.0%}")
    if proposed > rules.single_position:
        violations.append(f"单票仓位 {proposed:.0%} 超过上限 {rules.single_position:.0%}")
    if industry_position + proposed > rules.industry_limit:
        violations.append(f"行业仓位 {industry_position + proposed:.0%} 超过上限 {rules.industry_limit:.0%}")
    return violations


def check_stop_loss(plan: dict, current_price: float) -> bool:
    """触发止损：价格 <= 计划止损位。"""
    sl = plan.get("stop_loss")
    if sl is None:
        return False
    return current_price <= float(sl)


def check_drawdown(equity_peak: float, equity_now: float, rules: RiskRules | None = None) -> bool:
    """账户回撤超阈值则触发强制降仓信号（兼容旧接口；详细阶段见 limits.drawdown_stage）。"""
    rules = rules or RiskRules()
    if equity_peak <= 0:
        return False
    return (equity_peak - equity_now) / equity_peak > rules.max_drawdown


def drawdown_stage(equity_peak: float, equity_now: float) -> dict:
    """回撤阶梯（v3 16）：warn 5% / reduce 8% / clear 12% / halt 15%。"""
    from invest.discipline.limits import drawdown_stage as _stage
    return _stage(equity_peak, equity_now)
