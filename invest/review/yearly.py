"""年度复盘（2026-08-15 升级，TODO 阶段3）：规则有效性全维度检验。

- 等级单调性：候选池等级（core/track/rest）与历史胜率/收益是否单调；
- 凯利参数校准：按实际成交统计胜率/赔率，对比当前凯利参数；
- 四套权重区分度：评级-仓位映射下不同市场状态的实际仓位分布是否区分；
- 规则变更归档：rule_versions 本年变更记录汇总；
- 错误分类汇总：error_classify 五类分布。
"""
from __future__ import annotations

import json
import sqlite3


def _level_stats(conn: sqlite3.Connection) -> dict:
    """按候选池等级统计交易胜率/盈亏（等级单调性检验的数据）。"""
    rows = conn.execute(
        """SELECT cp.level AS level, tr.pnl AS pnl
           FROM trade_records tr
           JOIN trade_plans tp ON tr.plan_id = tp.id
           JOIN candidate_pool cp ON tp.symbol = cp.symbol
           WHERE tr.pnl IS NOT NULL"""
    ).fetchall()
    stats: dict[str, dict] = {}
    for r in rows:
        level = r["level"] or "rest"
        s = stats.setdefault(level, {"n": 0, "wins": 0, "total": 0.0})
        s["n"] += 1
        pnl = float(r["pnl"])
        s["total"] += pnl
        if pnl > 0:
            s["wins"] += 1
    for s in stats.values():
        s["win_rate"] = round(s["wins"] / s["n"], 4) if s["n"] else 0.0
        s["total"] = round(s["total"], 2)
    return stats


def level_monotonicity(conn: sqlite3.Connection) -> dict:
    """等级单调性检验：core 胜率 >= track >= rest 视为单调。"""
    stats = _level_stats(conn)
    order = ("core", "track", "rest")
    wrs = [stats[l]["win_rate"] for l in order if l in stats and stats[l]["n"] > 0]
    monotonic = all(wrs[i] >= wrs[i + 1] for i in range(len(wrs) - 1)) if len(wrs) >= 2 else None
    return {
        "stats": stats,
        "monotonic": monotonic,
        "note": "等级单调" if monotonic else ("样本不足" if monotonic is None else "等级不单调：需审视等级判定"),
    }


def kelly_calibration(conn: sqlite3.Connection) -> dict:
    """凯利参数校准：按实际成交统计胜率/赔率，与 kelly.py 默认对比。"""
    from invest.discipline.kelly import kelly_decision, wilson_lower
    rows = conn.execute(
        "SELECT pnl FROM trade_records WHERE pnl IS NOT NULL"
    ).fetchall()
    pnls = [float(r["pnl"]) for r in rows]
    if not pnls:
        return {"ok": False, "note": "无成交样本"}
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    gains = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    odds = (sum(gains) / len(gains)) / abs(sum(losses) / len(losses)) if gains and losses else 1.0
    decision = kelly_decision(n, wins, odds)
    return {
        "ok": True,
        "n": n,
        "win_rate": round(wins / n, 4),
        "odds": round(odds, 3),
        "wilson_lower": decision["wilson_lower"],
        "kelly_fraction": decision["fraction"],
        "kelly_enabled": decision["enabled"],
        "note": "启用置信下界凯利" if decision["enabled"] else "维持固定风险",
    }


def weight_discrimination(conn: sqlite3.Connection) -> dict:
    """四套权重区分度：评级-仓位映射在不同市场状态下的仓位上限分布。"""
    from invest.config import load_yaml_config
    mapping = load_yaml_config().get("rating_position_map", {})
    values = []
    for market in ("attack", "neutral", "defense"):
        row = [mapping.get(market, {}).get(m, 0.0) for m in ("loose", "neutral", "tight")]
        values.append({"market": market, "loose": row[0], "neutral": row[1], "tight": row[2]})
    # 区分度：attack.loose(0.4) vs defense.tight(0.05) 的跨度
    spread = values[0]["loose"] - values[2]["tight"] if len(values) == 3 else 0.0
    return {
        "positions": values,
        "spread": round(spread, 3),
        "note": f"进攻-宽松({values[0]['loose']}) vs 防守-收紧({values[2]['tight']})，跨度 {spread:.0%}",
    }


def rule_change_archive(conn: sqlite3.Connection, year: str | None = None) -> list[dict]:
    """规则变更归档：rule_versions 本年变更记录。"""
    import datetime as dt
    year = year or str(dt.date.today().year)
    rows = conn.execute(
        """SELECT rule_name, version, change_reason, effective_date, status
           FROM rule_versions
           WHERE effective_date LIKE ? OR created_at LIKE ?
           ORDER BY id""",
        (f"{year}%", f"{year}%"),
    ).fetchall()
    return [dict(r) for r in rows]


def yearly_review(conn: sqlite3.Connection) -> dict:
    """年度复盘主函数（替换旧模板）：全维度检验汇总。"""
    mono = level_monotonicity(conn)
    kelly = kelly_calibration(conn)
    weights = weight_discrimination(conn)
    archive = rule_change_archive(conn)
    try:
        from .error_classify import error_report
        errors = error_report(conn)
    except Exception:  # noqa: BLE001
        errors = {"ok": False, "note": "错误分类不可用"}
    # 兼容旧接口：backtest_summary（回测运行记录摘要）
    bt_rows = conn.execute(
        "SELECT rule_type, dataset_range, metrics_json FROM backtest_runs ORDER BY id DESC LIMIT 6"
    ).fetchall()
    backtest_summary = []
    for r in bt_rows:
        try:
            metrics = json.loads(r["metrics_json"] or "[]")
        except (ValueError, TypeError):
            metrics = []
        backtest_summary.append({
            "rule_type": r["rule_type"],
            "dataset": r["dataset_range"],
            "summary": metrics[:3],
        })
    return {
        "backtest_summary": backtest_summary,
        "level_monotonicity": mono,
        "kelly_calibration": kelly,
        "weight_discrimination": weights,
        "rule_changes": archive,
        "error_classification": errors,
        "suggestions": _suggestions(mono, kelly, errors),
    }


def _suggestions(mono: dict, kelly: dict, errors: dict) -> list[str]:
    out = []
    if mono.get("monotonic") is False:
        out.append("等级不单调：core 胜率未高于 track/rest，审视等级判定标准")
    if kelly.get("ok") and kelly.get("kelly_enabled"):
        out.append(f"凯利已启用（Wilson 下界 {kelly['wilson_lower']:.1%}），按格子逐个校准")
    elif kelly.get("ok"):
        out.append("维持固定风险：样本/胜率未达凯利门槛")
    if errors.get("ok"):
        cats = errors.get("categories", {})
        bad = [c for c in ("execution", "data_error", "liquidity") if c in cats]
        if bad:
            out.append(f"存在非规则内亏损类别：{', '.join(bad)}，落实对应改进建议")
    return out
