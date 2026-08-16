"""仓位与计划执行（TODO 1.5，2026-08-15）。

- 固定风险 R：S=0.8% / A=0.6% / B=0.35% 净值损失，不启用凯利（v3 11.6）；
- 单笔硬上限：等级帽（S/A/B）、单只个股 10%、单只 ETF 15%；
- 从卡片生成交易计划：引用卡片 ID、入场区间、止损、目标、禁追价。
"""
from __future__ import annotations

import sqlite3

# 固定风险 R（每笔允许的最大净值损失比例）
LEVEL_RISK = {"S": 0.008, "A": 0.006, "B": 0.0035, "C": 0.002}

# 等级仓位帽（占净值）
LEVEL_CAP = {"S": 0.20, "A": 0.15, "B": 0.10, "C": 0.05}

SINGLE_STOCK_CAP = 0.10   # 单只个股 10%
SINGLE_ETF_CAP = 0.15     # 单只 ETF 15%


def fixed_risk_position(
    level: str,
    equity: float,
    entry: float,
    stop_loss: float,
) -> dict:
    """固定风险 R 计算仓位。

    风险金额 = equity × R(level)；每股风险 = |entry - stop|；
    股数 = 风险金额 / 每股风险（向下取整到 100 股）；仓位 = 股数×entry/equity。
    """
    risk_frac = LEVEL_RISK.get(level.upper(), 0.0035)
    risk_amount = equity * risk_frac
    per_share_risk = abs(entry - stop_loss)
    if per_share_risk <= 0:
        return {"ok": False, "note": "入场=止损，风险为 0"}
    qty = int(risk_amount / per_share_risk // 100 * 100)
    if qty <= 0:
        return {"ok": False, "note": "风险预算不足以买 1 手（100 股）"}
    position_value = qty * entry
    position_frac = position_value / equity if equity else 0.0
    cap = min(LEVEL_CAP.get(level.upper(), 0.10), SINGLE_STOCK_CAP)
    capped = min(position_frac, cap)
    return {
        "ok": True,
        "level": level.upper(),
        "risk_fraction": risk_frac,
        "risk_amount": round(risk_amount, 2),
        "qty": qty,
        "position_value": round(position_value, 2),
        "position_fraction": round(position_frac, 4),
        "cap": cap,
        "final_fraction": round(capped, 4),
        "capped": capped < position_frac,
    }


def single_cap(symbol: str, is_etf: bool = False) -> float:
    """单笔硬上限：ETF 15%，个股 10%。"""
    return SINGLE_ETF_CAP if is_etf else SINGLE_STOCK_CAP


def create_plan_from_card(
    conn: sqlite3.Connection,
    card_id: int,
    equity: float | None = None,
    note: str = "",
) -> dict:
    """从已锁定卡片生成交易计划（引用卡片 ID、入场区间、止损、禁追价）。"""
    row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    if row is None:
        raise ValueError(f"卡片 {card_id} 不存在")
    if row["status"] != "locked":
        raise ValueError("只有 locked 卡片可生成交易计划")
    if row["stop_loss"] is None or not (row["entry_range"] or ""):
        raise ValueError("卡片缺少止损/入场区间，禁止建计划")
    from .plans import create_plan
    plan = create_plan(
        conn,
        symbol=row["symbol"],
        ref_viewpoint_id=None,
        buy_range=row["entry_range"],
        target_position=None,
        stop_loss=row["stop_loss"],
        take_profit=str(row["target"]) if row["target"] else "",
        invalid_condition=row["falsify"] or "",
    )
    # 记录卡片引用（deviation_note 或新增列；v1 用 review_note 关联）
    conn.execute(
        "UPDATE trade_plans SET invalid_condition = invalid_condition || ' | card_id=' || ? WHERE id=?",
        (str(card_id), plan["plan_id"]),
    )
    conn.commit()
    result = {"plan_id": plan["plan_id"], "symbol": row["symbol"], "card_id": card_id}
    if equity and equity > 0:
        # 按固定风险 R 计算建议仓位
        try:
            entry = float(row["entry_range"].split(",")[0])
        except (ValueError, IndexError):
            entry = float(row["entry_range"].replace("，", ",").split(",")[0])
        pos = fixed_risk_position(row["level"], equity, entry, row["stop_loss"])
        result["suggested_position"] = pos
    return result
