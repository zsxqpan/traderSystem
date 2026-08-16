"""对象池硬门槛与冻结名单（TODO 1.1，2026-08-15）。

硬门槛（入池前校验，任一不满足拒绝入池）：
- 非 ST/退市整理：symbol 不含 ST 前缀；
- 上市满 60 交易日：daily_bars 历史 >= 60 行；
- 20 日成交额满足参与率：最近 20 日均成交额 >= min_adv（默认 5000 万）。

冻结名单：复用 costs.mark_liquidity_breach / is_frozen（流动性违约冻结）；
另提供手工冻结 freeze_symbol / unfreeze_symbol（risk_rules rule_type='pool_freeze'）。
"""
from __future__ import annotations

import json
import sqlite3

MIN_ADV = 50_000_000      # 20 日均成交额下限（5000 万）
MIN_TRADING_DAYS = 60     # 上市最短交易日


def hard_gate_check(
    conn: sqlite3.Connection,
    symbol: str,
    min_adv: float = MIN_ADV,
    min_days: int = MIN_TRADING_DAYS,
) -> list[str]:
    """入池硬门槛校验，返回违规列表（空=通过）。"""
    violations: list[str] = []
    s = symbol.upper()
    if s.startswith(("ST", "*ST")):
        violations.append(f"{symbol} 为 ST/*ST，禁止入池")
    # 退市整理（常见后缀退/摘牌）
    if s.endswith("退"):
        violations.append(f"{symbol} 疑似退市整理，禁止入池")
    # 上市时长：daily_bars 行数
    try:
        n = conn.execute(
            "SELECT COUNT(*) c FROM daily_bars WHERE symbol=?", (symbol,)
        ).fetchone()["c"]
        if n < min_days:
            violations.append(f"{symbol} 上市交易日不足（{n}<{min_days}）")
    except Exception:  # noqa: BLE001
        violations.append(f"{symbol} 无法校验上市时长（无日线数据）")
    # 20 日均成交额
    try:
        rows = conn.execute(
            """SELECT amount FROM daily_bars WHERE symbol=?
               AND amount IS NOT NULL ORDER BY REPLACE(date,'-','') DESC LIMIT 20""",
            (symbol,),
        ).fetchall()
        if rows:
            adv = sum(float(r["amount"]) for r in rows) / len(rows)
            if adv < min_adv:
                violations.append(f"{symbol} 20日均成交额 {adv/1e8:.2f}亿 < 下限 {min_adv/1e8:.2f}亿")
        else:
            violations.append(f"{symbol} 无成交额数据")
    except Exception:  # noqa: BLE001
        violations.append(f"{symbol} 无法校验成交额")
    return violations


def check_and_add(
    conn: sqlite3.Connection,
    symbol: str,
    level: str = "track",
    industry: str = "",
    reason: str = "",
    min_adv: float = MIN_ADV,
    min_days: int = MIN_TRADING_DAYS,
    require_mispricing: bool = False,
    cheap_pct: float = 0.30,
) -> dict:
    """硬门槛校验后入池；不通过抛 ValueError 并记录否决（防选择偏差）。

    require_mispricing=True（榜单降级为「发现器」，[A]5）：候选还必须
    过错价必要条件（历史分位 < cheap_pct 或 Z 显著为负），否则记录否决并拒绝。
    """
    violations = hard_gate_check(conn, symbol, min_adv, min_days)
    if violations:
        from invest.data.pit import record_decision
        try:
            record_decision(
                conn, decision="reject", symbol=symbol, level=level,
                industry=industry, reason="硬门槛否决: " + "; ".join(violations),
            )
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(f"{symbol} 未通过硬门槛: {'; '.join(violations)}")
    if require_mispricing:
        from invest.discipline.spread import discover_eligible
        verdict = discover_eligible(conn, symbol, industry=industry, cheap_pct=cheap_pct)
        if not verdict["eligible"]:
            from invest.data.pit import record_decision
            try:
                record_decision(
                    conn, decision="reject", symbol=symbol, level=level,
                    industry=industry, reason="错价必要条件否决: " + verdict["reason"],
                )
            except Exception:  # noqa: BLE001
                pass
            raise ValueError(f"{symbol} 未过错价必要条件: {verdict['reason']}")
    from .pool import add_to_pool
    return add_to_pool(conn, symbol, level=level, industry=industry, reason=reason)


def freeze_symbol(conn: sqlite3.Connection, symbol: str, reason: str = "") -> None:
    """手工冻结标的（禁止新开仓）。"""
    with conn:
        conn.execute(
            """INSERT INTO risk_rules(rule_type, params_json, enabled)
               VALUES('pool_freeze', ?, 1)""",
            (json.dumps({"symbol": symbol, "reason": reason}, ensure_ascii=False),),
        )


def unfreeze_symbol(conn: sqlite3.Connection, symbol: str) -> int:
    """解冻标的，返回解冻条数。"""
    rows = conn.execute(
        "SELECT id, params_json FROM risk_rules WHERE rule_type='pool_freeze' AND enabled=1"
    ).fetchall()
    n = 0
    for r in rows:
        try:
            params = json.loads(r["params_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if params.get("symbol") == symbol:
            conn.execute("UPDATE risk_rules SET enabled=0 WHERE id=?", (r["id"],))
            n += 1
    conn.commit()
    return n


def is_frozen_symbol(conn: sqlite3.Connection, symbol: str) -> bool:
    """查询标的是否被冻结（pool_freeze 或 liquidity_freeze）。"""
    from .costs import is_frozen as _liquidity_frozen
    for rule_type in ("pool_freeze", "liquidity_freeze"):
        rows = conn.execute(
            "SELECT params_json FROM risk_rules WHERE rule_type=? AND enabled=1", (rule_type,)
        ).fetchall()
        for r in rows:
            try:
                params = json.loads(r["params_json"] or "{}")
            except (ValueError, TypeError):
                continue
            if params.get("symbol") == symbol:
                return True
    return _liquidity_frozen(conn, symbol)


def list_l2_industries(conn: sqlite3.Connection) -> list[str]:
    """L2 行业清单：从 industry_bars 去重取全部行业（申万口径）。"""
    rows = conn.execute(
        "SELECT DISTINCT industry FROM industry_bars ORDER BY industry"
    ).fetchall()
    return [r["industry"] for r in rows]
