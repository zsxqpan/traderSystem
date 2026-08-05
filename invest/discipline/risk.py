"""风控规则引擎：评级仓位上限、单票/行业集中度、止损触发、回撤阈值。"""
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


def check_position(
    conn: sqlite3.Connection,
    proposed: float,
    total_position: float = 0.0,
    industry_position: float = 0.0,
    rules: RiskRules | None = None,
) -> list[str]:
    """返回违规列表（空=通过）。"""
    rules = rules or load_risk_rules(conn)
    violations = []
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
    """账户回撤超阈值则触发强制降仓信号。"""
    rules = rules or RiskRules()
    if equity_peak <= 0:
        return False
    return (equity_peak - equity_now) / equity_peak > rules.max_drawdown