"""宏观与总闸（TODO 1.4 + [B]8 环境重评，2026-08-15）。

- 宏观环境评级（宽松/中性/收紧）：月度手工评分（config/ratings 表），留痕；
- 总仓位闸门 v1（v3 6.2）：ERP 分位 × 环境减法系数（宽松/中性 1.00，收紧 0.70）；
- 环境重评触发条件（[B]8，2026-08-15 落地）：数据驱动自动提示重评——
  1) ERP 跨分位：全A中位PE近10年分位 跌破 0.2（变便宜）或升破 0.8（变贵）；
  2) 社融拐点：最新社融增量较上月转负（增量下降）；
  3) 10Y 利率周变动 > 20bp（中国国债收益率10年）。
- 黑天鹅戒断清单：预定义触发条件（暴跌/流动性危机/政策黑天鹅），
  触发后动作：总闸减半、禁新开仓、24h 书面复评。
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
import sqlite3

_CN_MONTH = re.compile(r"(\d{4})年(\d{1,2})月")

# 环境减法系数（宏观只做减法，不给方向加分，v3 6.2）
ENV_FACTOR = {"宽松": 1.00, "中性": 1.00, "收紧": 0.70}

# 黑天鹅触发条件（v1 清单）
BLACK_SWAN_TRIGGERS = [
    ("index_crash", "单日指数跌幅 > 5%"),
    ("limit_down_tide", "跌停家数 > 500（流动性危机）"),
    ("policy_shock", "重大政策黑天鹅（监管/地缘/金融风险）"),
    ("liquidity_freeze", "连续 3 日成交额骤降 > 50%"),
]

# 环境重评触发阈值（[B]8）
ERP_PCT_LO = 0.20      # 全A PE 近10年分位跌破 0.20 → 变便宜，提示重评
ERP_PCT_HI = 0.80      # 升破 0.80 → 变贵，提示重评
SOCIAL_FIN_DROP_BP = 0.0   # 社融增量环比转负即触发
BOND_10Y_WEEK_BP = 20.0    # 10Y 利率周变动 > 20bp（0.20 个百分点）


def macro_rating(conn: sqlite3.Connection) -> str:
    """当前宏观评级（ratings 表 macro 最新值），无则默认中性。"""
    row = conn.execute(
        "SELECT value FROM ratings WHERE kind='macro' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return row["value"] if row and row["value"] in ENV_FACTOR else "中性"


def env_factor(env: str) -> float:
    """环境减法系数。"""
    return ENV_FACTOR.get(env, 1.00)


def _comparable_macro_date(date_str: str) -> str | None:
    """macro_series.date → YYYY-MM-DD，供 as_of 截断；中文月份落到当月最后一天。"""
    s = str(date_str or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    found = _CN_MONTH.match(s)
    if found:
        year, month = int(found.group(1)), int(found.group(2))
        last = calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-{last:02d}"
    return None


def _pick_macro(
    conn: sqlite3.Connection,
    indicator: str,
    *,
    before: str | None = None,
    on_or_before: str | None = None,
) -> dict | None:
    rows = conn.execute(
        "SELECT date, value FROM macro_series WHERE indicator=? AND value IS NOT NULL",
        (indicator,),
    ).fetchall()
    picked = None
    picked_key: str | None = None
    before_key = _comparable_macro_date(before) if before else None
    for row in rows:
        key = _comparable_macro_date(row["date"])
        if key is None:
            continue
        if on_or_before and key > on_or_before:
            continue
        if before_key and key >= before_key:
            continue
        if picked_key is None or key > picked_key:
            picked, picked_key = row, key
    return {"date": picked["date"], "value": float(picked["value"])} if picked else None


def _latest_macro(
    conn: sqlite3.Connection,
    indicator: str,
    as_of: str | None = None,
) -> dict | None:
    """macro_series 最新一条指标。as_of 有值时按可比日期截断，不偷看之后的点。"""
    if as_of:
        return _pick_macro(conn, indicator, on_or_before=as_of)
    row = conn.execute(
        "SELECT date, value FROM macro_series WHERE indicator=? AND value IS NOT NULL "
        "ORDER BY date DESC LIMIT 1",
        (indicator,),
    ).fetchone()
    return {"date": row["date"], "value": float(row["value"])} if row else None


def _prev_macro(
    conn: sqlite3.Connection,
    indicator: str,
    date: str,
    as_of: str | None = None,
) -> dict | None:
    """该指标在 date 之前的最近一条（用于环比/周变动）。"""
    if as_of:
        return _pick_macro(conn, indicator, before=date, on_or_before=as_of)
    row = conn.execute(
        "SELECT date, value FROM macro_series WHERE indicator=? AND value IS NOT NULL "
        "AND date < ? ORDER BY date DESC LIMIT 1",
        (indicator, date),
    ).fetchone()
    return {"date": row["date"], "value": float(row["value"])} if row else None


def check_env_retrigger(conn: sqlite3.Connection, as_of: str | None = None) -> dict:
    """环境重评触发检查（[B]8）：返回触发列表与提示。

    任一条件触发即建议人工复核宏观评级；不自动改评级（宏观只做减法）。
    as_of 有值时只看该日及之前的 macro_series，历史回放不得偷看最新点。
    返回 {triggers: [...], n: int, data: {...}}。
    """
    triggers: list[str] = []
    data: dict = {}

    # 1) ERP 跨分位（用全A中位PE近10年分位作代理：高=贵=ERP低）
    pe_pct = _latest_macro(conn, "全A中位PE近10年分位", as_of=as_of)
    if pe_pct:
        data["pe_pct"] = pe_pct["value"]
        if pe_pct["value"] < ERP_PCT_LO:
            triggers.append(
                f"ERP 跨分位：全A中位PE近10年分位 {pe_pct['value']:.2f} < {ERP_PCT_LO:.2f}（股票变便宜，评估是否上调评级）"
            )
        elif pe_pct["value"] > ERP_PCT_HI:
            triggers.append(
                f"ERP 跨分位：全A中位PE近10年分位 {pe_pct['value']:.2f} > {ERP_PCT_HI:.2f}（股票变贵，评估是否下调评级）"
            )
    else:
        data["pe_pct"] = None

    # 2) 社融拐点：最新较上月转负（增量下降）
    sf = _latest_macro(conn, "社会融资规模增量", as_of=as_of)
    if sf:
        data["social_fin"] = sf
        prev_sf = _prev_macro(conn, "社会融资规模增量", sf["date"], as_of=as_of)
        if prev_sf and sf["value"] < prev_sf["value"]:
            triggers.append(
                f"社融拐点：社融增量 {prev_sf['value']:.0f} → {sf['value']:.0f} 亿元（环比转负，评估信用环境收紧）"
            )
    else:
        data["social_fin"] = None

    # 3) 10Y 利率周变动 > 20bp
    y10 = _latest_macro(conn, "中国国债收益率10年", as_of=as_of)
    if y10:
        data["bond_10y"] = y10
        prev_y10 = None
        try:
            dt.date.fromisoformat(y10["date"])
            prev_y10 = _prev_macro(conn, "中国国债收益率10年", y10["date"], as_of=as_of)
        except ValueError:
            prev_y10 = None
        if prev_y10:
            change_bp = (y10["value"] - prev_y10["value"]) * 100.0  # % → bp
            if abs(change_bp) > BOND_10Y_WEEK_BP:
                direction = "上行" if change_bp > 0 else "下行"
                triggers.append(
                    f"10Y 利率周变动 {change_bp:+.1f}bp > {BOND_10Y_WEEK_BP:.0f}bp（{direction}，评估流动性环境）"
                )
    else:
        data["bond_10y"] = None

    return {"triggers": triggers, "n": len(triggers), "data": data}


def env_retrigger_text(result: dict) -> str:
    """触发结果 → 推送文本（无触发返回空串，供调度器静默）。"""
    if not result["triggers"]:
        return ""
    lines = ["[B8] 环境重评触发（数据驱动，建议人工复核宏观评级）"]
    lines += [f"- {t}" for t in result["triggers"]]
    return "\n".join(lines)


def position_gate(
    base_position: float,
    env: str = "",
    erp_pct: float | None = None,
    erp_scale: float = 1.0,
) -> dict:
    """总仓位闸门 v1：ERP 分位 × 环境系数。

    base_position: 评级映射的基础仓位上限；
    erp_pct: ERP 分位（0-1，高=股票便宜，可加仓；可空）；
    erp_scale: ERP 分位对仓位的乘数（0.8-1.2，v1 简化线性）。
    返回 {gate_position, env, env_factor, erp_pct, reason}。
    """
    factor = env_factor(env)
    if erp_pct is not None:
        # ERP 高（股票便宜）时允许接近基准，低时打折：scale = 0.8 + 0.4*erp_pct
        erp_scale = 0.8 + 0.4 * max(0.0, min(1.0, erp_pct))
    gate = base_position * factor * erp_scale
    return {
        "gate_position": round(gate, 4),
        "env": env or "中性",
        "env_factor": factor,
        "erp_pct": erp_pct,
        "erp_scale": round(erp_scale, 3),
        "reason": f"基准 {base_position:.0%} × 环境系数 {factor:.2f} × ERP乘数 {erp_scale:.2f} = {gate:.1%}",
    }


def check_black_swan(
    index_change_pct: float | None = None,
    limit_down_count: int | None = None,
    volume_drop_pct: float | None = None,
    policy_shock: bool = False,
) -> list[str]:
    """黑天鹅触发检查：返回触发列表（空=未触发）。"""
    triggered: list[str] = []
    if index_change_pct is not None and index_change_pct <= -0.05:
        triggered.append(f"指数单日跌幅 {index_change_pct:.1%} > 5%（{BLACK_SWAN_TRIGGERS[0][1]}）")
    if limit_down_count is not None and limit_down_count > 500:
        triggered.append(f"跌停 {limit_down_count} 家 > 500（{BLACK_SWAN_TRIGGERS[1][1]}）")
    if volume_drop_pct is not None and volume_drop_pct <= -0.50:
        triggered.append(f"成交额骤降 {volume_drop_pct:.0%}（{BLACK_SWAN_TRIGGERS[3][1]}）")
    if policy_shock:
        triggered.append(BLACK_SWAN_TRIGGERS[2][1])
    return triggered


def black_swan_actions(triggered: list[str]) -> list[str]:
    """黑天鹅触发后的强制动作：总闸减半、禁新开仓、24h 书面复评。"""
    if not triggered:
        return []
    return [
        "总闸减半：所有仓位上限 × 0.5",
        "禁新开仓：直至复评通过",
        "24h 内书面复评（记录触发条件、持仓暴露、应对）",
    ]


def apply_gate(
    conn: sqlite3.Connection,
    base_position: float,
    erp_pct: float | None = None,
    index_change_pct: float | None = None,
    limit_down_count: int | None = None,
    volume_drop_pct: float | None = None,
    policy_shock: bool = False,
) -> dict:
    """完整总闸：环境系数 × ERP × 黑天鹅降级。"""
    env = macro_rating(conn)
    gate = position_gate(base_position, env=env, erp_pct=erp_pct)
    swans = check_black_swan(index_change_pct, limit_down_count, volume_drop_pct, policy_shock)
    final = gate["gate_position"]
    if swans:
        final *= 0.5  # 总闸减半
    return {
        "final_gate": round(final, 4),
        "env": env,
        "gate": gate,
        "black_swans": swans,
        "actions": black_swan_actions(swans),
    }
