"""回撤/损失限额与压力测试（v3 16，2026-08-15）。

限额阶梯（相对账户峰值）：
- 回撤 5%：预警（减半、禁新开仓）；
- 回撤 8%：强减（降至半仓）；
- 回撤 12%：清仓（全部退出）；
- 回撤 15%：停摆（冻结，人工复盘后重启）。
- 单日亏损 2%：当日禁新开仓；
- 单周亏损 4%：本周禁新开仓。

压力测试场景（持仓组合净值模拟）：
- 全仓低开 5% / 跌停无法退出（-10%/-20% 深市创业板/北交所）；
- 相关性升至 0.8（组合等权模拟）；
- 流动性减半（ADV 减半 → 参与率翻倍，无法退出标记）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DrawdownLevels:
    warn: float = 0.05    # 5%：预警
    reduce: float = 0.08  # 8%：强减半仓
    clear: float = 0.12   # 12%：清仓
    halt: float = 0.15    # 15%：停摆

    daily_loss: float = 0.02   # 单日 2%
    weekly_loss: float = 0.04  # 单周 4%


def drawdown_stage(
    peak: float,
    equity: float,
    levels: DrawdownLevels | None = None,
) -> dict:
    """回撤阶段判定。返回 {stage, drawdown, actions}。"""
    levels = levels or DrawdownLevels()
    if peak <= 0:
        return {"stage": "normal", "drawdown": 0.0, "actions": []}
    dd = (peak - equity) / peak
    actions: list[str] = []
    if dd >= levels.halt:
        stage = "halt"
        actions = ["停摆：全部冻结，人工复盘后重启"]
    elif dd >= levels.clear:
        stage = "clear"
        actions = ["清仓：全部退出，等待企稳"]
    elif dd >= levels.reduce:
        stage = "reduce"
        actions = ["强减：降至半仓"]
    elif dd >= levels.warn:
        stage = "warn"
        actions = ["预警：减半新开仓、禁新开仓"]
    else:
        stage = "normal"
    return {"stage": stage, "drawdown": round(dd, 4), "actions": actions}


def daily_loss_check(daily_pnl: float, equity: float, levels: DrawdownLevels | None = None) -> dict:
    """单日亏损限额。返回 {blocked, reason}。"""
    levels = levels or DrawdownLevels()
    if equity <= 0:
        return {"blocked": True, "reason": "权益为 0"}
    loss_pct = -daily_pnl / equity
    if loss_pct >= levels.daily_loss:
        return {"blocked": True, "reason": f"单日亏损 {loss_pct:.1%} >= {levels.daily_loss:.0%}：当日禁新开仓"}
    return {"blocked": False, "reason": ""}


def weekly_loss_check(week_pnl: float, equity: float, levels: DrawdownLevels | None = None) -> dict:
    """单周亏损限额。返回 {blocked, reason}。"""
    levels = levels or DrawdownLevels()
    if equity <= 0:
        return {"blocked": True, "reason": "权益为 0"}
    loss_pct = -week_pnl / equity
    if loss_pct >= levels.weekly_loss:
        return {"blocked": True, "reason": f"单周亏损 {loss_pct:.1%} >= {levels.weekly_loss:.0%}：本周禁新开仓"}
    return {"blocked": False, "reason": ""}


# ---------- 压力测试 ----------

@dataclass
class StressScenario:
    name: str
    shock: float          # 组合净值冲击（如 -0.10 = 低开 10%）
    note: str = ""


STD_SCENARIOS = [
    StressScenario("全仓低开5%", -0.05, "次日低开 5% 全部持仓"),
    StressScenario("跌停无法退出-沪深主板", -0.10, "主板跌停 -10% 无法卖出"),
    StressScenario("跌停无法退出-创业板/北交所", -0.20, "创业板 -20% / 北交所 -30% 无法卖出"),
    StressScenario("相关性升至0.8", -0.15, "组合分散失效，等权模拟集中损失"),
    StressScenario("流动性减半", -0.08, "ADV 减半导致参与率翻倍、无法按计划退出"),
]


def stress_test(equity: float, scenarios: list[StressScenario] | None = None) -> list[dict]:
    """压力测试：每个场景下组合净值与回撤阶段。"""
    scenarios = scenarios or STD_SCENARIOS
    out = []
    for sc in scenarios:
        shocked = equity * (1 + sc.shock)
        dd = -sc.shock  # 假设峰值=当前权益，冲击即回撤
        stage = drawdown_stage(equity, shocked)
        out.append({
            "scenario": sc.name,
            "note": sc.note,
            "shock": sc.shock,
            "equity_after": round(shocked, 2),
            "drawdown": round(dd, 4),
            "stage": stage["stage"],
            "actions": stage["actions"],
        })
    return out


def worst_scenario(stress: list[dict]) -> dict:
    """返回最坏场景。"""
    if not stress:
        return {}
    return max(stress, key=lambda x: x["drawdown"])
