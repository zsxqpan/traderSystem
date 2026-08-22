"""BCS/VMS 双百分制评估 + 一票否决检查（2026-08-15，TODO 阶段3）。

- BCS（Backtest Completeness Score）：回测完整性评分（0-100）
  维度：样本量 / 样本外验证 / 成本建模 / 参数稳健性 / 数据质量；
- VMS（Validation Maturity Score）：验证成熟度评分（0-100）
  维度：规则版本管理 / 因子检验 / 纪律执行 / 复盘闭环 / 自动化降级；
- veto_check(): 一票否决检查（任一触发即整体否决）。
"""
from __future__ import annotations

import sqlite3

# 一票否决条件（v3：数据失效即防守 / 纪律硬约束）
VETO_RULES = [
    ("realtime_stale", "实时行情失效（stale>0）时不得支撑 P0 决策"),
    ("pool_violation", "候选池满或核心关注超限仍开仓"),
    ("no_stop_loss", "无止损位交易计划"),
    ("rogue_trade", "计划外交易（无 plan_id 或不在候选池）"),
    ("data_stale", "日线数据陈旧（>7 天）仍生成决策"),
]


def bcs_score(
    n_samples: int = 0,
    oos_done: bool = False,
    cost_model: bool = False,
    param_robust: bool = False,
    data_quality: bool = False,
) -> dict:
    """回测完整性评分（0-100）。各维度满分：样本 30 / 样本外 25 / 成本 20 / 稳健 15 / 数据 10。"""
    s = 0.0
    detail: list[str] = []
    # 样本量：<20 笔 0 分；20-100 线性；>500 满分 30
    if n_samples >= 500:
        s += 30
        detail.append(f"样本量 {n_samples}（满分）")
    elif n_samples >= 20:
        s += 15 + 15 * (n_samples - 20) / 480
        detail.append(f"样本量 {n_samples}（部分）")
    else:
        detail.append(f"样本量 {n_samples}（不足 20）")
    # 样本外
    if oos_done:
        s += 25
        detail.append("样本外验证")
    else:
        detail.append("缺样本外验证")
    # 成本建模
    if cost_model:
        s += 20
        detail.append("成本建模")
    else:
        detail.append("缺成本建模")
    # 参数稳健性
    if param_robust:
        s += 15
        detail.append("参数稳健性检验")
    else:
        detail.append("缺参数稳健性")
    # 数据质量
    if data_quality:
        s += 10
        detail.append("数据质量达标")
    else:
        detail.append("数据质量未达标")
    return {
        "score": round(s, 1),
        "detail": detail,
        "grade": "A" if s >= 80 else ("B" if s >= 60 else ("C" if s >= 40 else "D")),
    }


def vms_score(
    versioned: bool = False,
    factor_tested: bool = False,
    discipline_tracked: bool = False,
    review_loop: bool = False,
    auto_degrade: bool = False,
) -> dict:
    """验证成熟度评分（0-100）。各维度满分：版本 25 / 因子 25 / 纪律 20 / 复盘 15 / 降级 15。"""
    s = 0.0
    detail: list[str] = []
    if versioned:
        s += 25
        detail.append("规则版本管理")
    else:
        detail.append("缺规则版本管理")
    if factor_tested:
        s += 25
        detail.append("因子有效性检验")
    else:
        detail.append("缺因子检验")
    if discipline_tracked:
        s += 20
        detail.append("纪律执行留痕")
    else:
        detail.append("缺纪律留痕")
    if review_loop:
        s += 15
        detail.append("复盘闭环")
    else:
        detail.append("缺复盘闭环")
    if auto_degrade:
        s += 15
        detail.append("自动化降级")
    else:
        detail.append("缺自动化降级")
    return {
        "score": round(s, 1),
        "detail": detail,
        "grade": "A" if s >= 80 else ("B" if s >= 60 else ("C" if s >= 40 else "D")),
    }


def veto_check(
    conn: sqlite3.Connection,
    realtime_ok: bool = True,
    data_fresh: bool = True,
) -> dict:
    """一票否决检查：任一条件触发即整体否决。

    自动检测：realtime 留痕（job_runs）、候选池容量、无止损计划、计划外交易。
    """
    violations: list[str] = []
    # 1) 实时行情失效
    if not realtime_ok:
        violations.append(VETO_RULES[0][1])
    # 2) 数据陈旧
    if not data_fresh:
        violations.append(VETO_RULES[4][1])
    # 3) 候选池超限
    try:
        row = conn.execute(
            "SELECT COUNT(*) c FROM candidate_pool WHERE out_date IS NULL"
        ).fetchone()
        core = conn.execute(
            "SELECT COUNT(*) c FROM candidate_pool WHERE level='core' AND out_date IS NULL"
        ).fetchone()
        if row["c"] > 20:
            violations.append("候选池满（>20）仍开仓")
        if core["c"] > 10:
            violations.append("核心关注超限（>10）仍开仓")
    except Exception:
        pass
    # 4) 无止损计划
    try:
        n = conn.execute(
            "SELECT COUNT(*) c FROM trade_plans WHERE status='active' AND stop_loss IS NULL"
        ).fetchone()["c"]
        if n:
            violations.append(f"存在 {n} 个无止损的 active 计划")
    except Exception:
        pass
    # 5) 计划外交易
    try:
        n = conn.execute(
            """SELECT COUNT(*) c FROM trade_records tr
               LEFT JOIN trade_plans tp ON tr.plan_id = tp.id
               WHERE tp.id IS NULL"""
        ).fetchone()["c"]
        if n:
            violations.append(f"存在 {n} 条计划外交易记录")
    except Exception:
        pass
    return {
        "passed": not violations,
        "violations": violations,
        "n_veto": len(violations),
    }


def full_assessment(
    conn: sqlite3.Connection,
    n_samples: int = 0,
    oos_done: bool = False,
    cost_model: bool = True,
    param_robust: bool = False,
    versioned: bool = True,
    factor_tested: bool = True,
    discipline_tracked: bool = True,
    review_loop: bool = True,
    auto_degrade: bool = True,
) -> dict:
    """季度完整评估：BCS + VMS + 一票否决。"""
    # 数据质量自动检测
    data_quality = True
    try:
        from invest.data.pit import quality_report
        report = quality_report(conn)
        data_quality = all(st == "valid" for st, _ in report.values())
    except Exception:
        data_quality = False
    # 实时行情健康自动检测
    realtime_ok = True
    try:
        from invest.config import get_settings
        from invest.data.realtime import realtime_health
        realtime_ok = realtime_health(get_settings().db_path).get("ok", False)
    except Exception:
        realtime_ok = False

    bcs = bcs_score(
        n_samples=n_samples, oos_done=oos_done, cost_model=cost_model,
        param_robust=param_robust, data_quality=data_quality,
    )
    vms = vms_score(
        versioned=versioned, factor_tested=factor_tested,
        discipline_tracked=discipline_tracked, review_loop=review_loop,
        auto_degrade=auto_degrade,
    )
    veto = veto_check(conn, realtime_ok=realtime_ok, data_fresh=data_quality)
    # kill-gate 击杀门禁（2026-08-16）：实盘交易样本必须过硬性风险门槛
    kill_gate = None
    try:
        from invest.discipline.kill_gate import kill_gate_check
        kill_gate = kill_gate_check(conn=conn)
    except Exception:
        kill_gate = {"passed": False, "note": "kill-gate 计算失败", "metrics": {}}
    overall = "通过" if (bcs["grade"] in ("A", "B") and vms["grade"] in ("A", "B")
                         and veto["passed"] and (kill_gate is None or kill_gate["passed"])) else "不通过"
    return {
        "bcs": bcs,
        "vms": vms,
        "veto": veto,
        "kill_gate": kill_gate,
        "overall": overall,
    }
