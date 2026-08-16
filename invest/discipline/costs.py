"""A 股交易成本模型 + 可交易性校验（TODO 2.4 执行与成本，2026-08-15）。

成本模型（逐笔）：
- 佣金：默认万 2.5，最低 5 元（沪/深/北 2023-08 起均免最低 5 元限制——按保守 5 元计）；
- 印花税：卖出 0.05%（2023-08-28 减半后）；
- 过户费：0.001%（双向，沪市历史 0.002%，深市 2022 起 0.001%，取 0.001%）；
- 滑点：默认 0.1%（可配）；冲击成本：默认 0（小单忽略，可配）。

可交易性校验（开仓前）：
- T+1：当日买入当日不可卖出（sell 校验 buy_date）；
- 涨跌停：价格不得超过昨收 ±10%（ST ±5%，北交所 ±30%）——无法成交场景识别；
- ADV 参与率：单笔金额 / 20 日均成交额 <= 上限（默认 5%）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import pandas as pd

from invest.config import load_yaml_config


@dataclass
class CostParams:
    commission_rate: float = 0.00025   # 佣金费率
    commission_min: float = 5.0        # 最低佣金
    stamp_tax_rate: float = 0.0005     # 印花税（卖出，2023-08 减半后 0.05%）
    transfer_fee_rate: float = 0.00001 # 过户费（双向 0.001%）
    slippage_rate: float = 0.001       # 滑点（默认 0.1%）
    impact_rate: float = 0.0           # 冲击成本（默认 0，小单忽略）


@dataclass
class TradeCost:
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    slippage: float = 0.0
    impact: float = 0.0

    @property
    def total(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee + self.slippage + self.impact

    def breakdown(self) -> str:
        return (
            f"佣金{self.commission:.2f}+印花税{self.stamp_tax:.2f}+过户费{self.transfer_fee:.2f}"
            f"+滑点{self.slippage:.2f}+冲击{self.impact:.2f}=总{self.total:.2f}"
        )


def load_cost_params(config: dict | None = None) -> CostParams:
    config = config or load_yaml_config()
    c = config.get("costs", {})
    return CostParams(
        commission_rate=float(c.get("commission_rate", 0.00025)),
        commission_min=float(c.get("commission_min", 5.0)),
        stamp_tax_rate=float(c.get("stamp_tax_rate", 0.0005)),
        transfer_fee_rate=float(c.get("transfer_fee_rate", 0.00001)),
        slippage_rate=float(c.get("slippage_rate", 0.001)),
        impact_rate=float(c.get("impact_rate", 0.0)),
    )


def compute_cost(
    price: float,
    qty: int,
    action: str,
    params: CostParams | None = None,
) -> TradeCost:
    """计算一笔成交的成本。action: buy/sell。"""
    params = params or load_cost_params()
    amount = price * qty
    commission = max(amount * params.commission_rate, params.commission_min)
    stamp_tax = amount * params.stamp_tax_rate if action == "sell" else 0.0
    transfer_fee = amount * params.transfer_fee_rate
    slippage = amount * params.slippage_rate
    impact = amount * params.impact_rate
    return TradeCost(
        commission=round(commission, 2),
        stamp_tax=round(stamp_tax, 2),
        transfer_fee=round(transfer_fee, 2),
        slippage=round(slippage, 2),
        impact=round(impact, 2),
    )


def round_lot(qty: int) -> int:
    """A 股整手校验：100 的整数倍（科创板 200 起、可 1 股递增——保守按 100 整手）。"""
    if qty <= 0:
        raise ValueError("数量必须为正")
    if qty % 100 != 0:
        raise ValueError(f"A 股买入数量必须为 100 的整数倍（实际 {qty}）")
    return qty


def limit_pct(symbol: str) -> float:
    """涨跌停幅度：ST ±5%，北交所(4/8 开头) ±30%，其余 ±10%。"""
    s = symbol.lower()
    if s.startswith(("4", "8")):
        return 0.30
    return 0.05 if "st" in s.lower() or "*st" in s.lower() else 0.10


def check_tradable(
    conn: sqlite3.Connection,
    symbol: str,
    action: str,
    price: float,
    qty: int,
    prev_close: float | None = None,
    adv: float | None = None,
    buy_date: str | None = None,
    adv_participation_limit: float = 0.05,
) -> list[str]:
    """可交易性校验，返回违规列表（空=可交易）。

    - 数量整手校验（round_lot）；
    - T+1：sell 时若 buy_date 为今天则禁止卖出；
    - 涨跌停：|price/prev_close - 1| > limit_pct 视为无法成交；
    - ADV 参与率：单笔金额 / 20日均成交额 > 上限视为冲击过大。
    """
    violations: list[str] = []
    try:
        round_lot(qty)
    except ValueError as exc:
        violations.append(str(exc))
    if action == "sell" and buy_date:
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        if str(buy_date) == today:
            violations.append(f"T+1 限制：{symbol} 当日买入不可卖出")
    if prev_close and prev_close > 0:
        limit = limit_pct(symbol)
        pct = abs(price / prev_close - 1)
        if pct > limit:
            violations.append(
                f"{symbol} 价格 {price} 偏离昨收 {prev_close} {pct:.1%} > 涨跌停 {limit:.0%}：无法成交"
            )
    if adv and adv > 0:
        amount = price * qty
        participation = amount / adv
        if participation > adv_participation_limit:
            violations.append(
                f"{symbol} 单笔参与率 {participation:.1%}（{amount:.0f}/{adv:.0f}）> 上限 {adv_participation_limit:.0%}：流动性冲击过大"
            )
    return violations


def fetch_prev_close_and_adv(conn: sqlite3.Connection, symbol: str) -> tuple[float | None, float | None]:
    """从 daily_bars 取昨收与 20 日均成交额（用于可交易性校验）。"""
    row = conn.execute(
        """SELECT close, amount FROM daily_bars
           WHERE symbol=? ORDER BY REPLACE(date,'-','') DESC LIMIT 20""",
        (symbol,),
    ).fetchall()
    if not row:
        return None, None
    prev_close = float(row[0]["close"])
    amounts = [float(r["amount"]) for r in row if r["amount"]]
    adv = sum(amounts) / len(amounts) if amounts else None
    return prev_close, adv


def record_cost(conn: sqlite3.Connection, record_id: int, cost: TradeCost) -> None:
    """把成本写入 trade_records.deviation_note（无独立成本列，附注留痕）。"""
    note = conn.execute(
        "SELECT deviation_note FROM trade_records WHERE id=?", (record_id,)
    ).fetchone()
    prefix = note["deviation_note"] + " | " if note and note["deviation_note"] else ""
    conn.execute(
        "UPDATE trade_records SET deviation_note=? WHERE id=?",
        (prefix + f"成本[{cost.breakdown()}]", record_id),
    )
    conn.commit()


def mark_liquidity_breach(conn: sqlite3.Connection, symbol: str, note: str = "") -> None:
    """止损触发但无法成交的闭环：标记流动性违约。

    在 candidate_pool.reason 附注 + risk_rules 写入冻结记录（rule_type='liquidity_freeze'）。
    冻结后该标的相关新开仓被 data_guard/check_position 拒绝（见 is_frozen）。
    """
    note = note or "止损触发但无法成交（跌停/流动性枯竭）"
    with conn:
        conn.execute(
            """UPDATE candidate_pool
               SET reason = reason || ' | 流动性违约: ' || ?
               WHERE symbol=?""",
            (note, symbol),
        )
        # 冻结记录：2026-08-15 起 rule_type='liquidity_freeze'，params 存 symbol/note
        import json as _json
        conn.execute(
            """INSERT INTO risk_rules(rule_type, params_json, enabled)
               VALUES('liquidity_freeze', ?, 1)""",
            (_json.dumps({"symbol": symbol, "note": note}, ensure_ascii=False),),
        )


def is_frozen(conn: sqlite3.Connection, symbol: str) -> bool:
    """查询标的是否被流动性冻结（risk_rules 中 liquidity_freeze 且 enabled=1）。"""
    import json as _json
    for row in conn.execute(
        "SELECT params_json FROM risk_rules WHERE rule_type='liquidity_freeze' AND enabled=1"
    ):
        try:
            params = _json.loads(row["params_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if params.get("symbol") == symbol:
            return True
    return False
