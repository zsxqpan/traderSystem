"""错误分类落地（2026-08-15，TODO 阶段3）。

交易亏损/偏差按五类归因：
- rule_loss:    规则内亏损（按计划执行、止损到位，属正常成本）；
- execution:    执行违规（偏离计划区间、追价、超仓）；
- model_failure: 模型失效（信号/因子失效导致）；
- data_error:   数据错误（行情/基本面数据错）；
- liquidity:    流动性损失（滑点、无法退出）。

- classify(): 单笔分类（基于记录字段）；
- error_report(): 汇总报告（各类 n/金额/占比 + 改进建议）。
"""
from __future__ import annotations

import sqlite3

CATEGORIES = ("rule_loss", "execution", "model_failure", "data_error", "liquidity")

CATEGORY_LABELS = {
    "rule_loss": "规则内亏损",
    "execution": "执行违规",
    "model_failure": "模型失效",
    "data_error": "数据错误",
    "liquidity": "流动性损失",
}

SUGGESTIONS = {
    "execution": "强化执行纪律：开仓前必须核对计划区间，禁止追价；重复违规冻结开仓权限",
    "model_failure": "检查因子/信号有效性（factor_eval），失效因子降权或剔除",
    "data_error": "核对数据源新鲜度（pit.quality_report），数据失效时禁止开仓",
    "liquidity": "提高 ADV 参与率门槛，避开低流动性标的；止损无法成交时标记流动性违约",
}


def classify(record: dict) -> str:
    """单笔交易错误分类。

    record 字段：deviation_note / actual_vs_plan / pnl / emotion_note。
    优先级：数据错误 > 执行违规 > 流动性 > 模型失效 > 规则内亏损。
    """
    note = str(record.get("deviation_note") or "")
    avp = str(record.get("actual_vs_plan") or "")
    emotion = str(record.get("emotion_note") or "")
    pnl = record.get("pnl")

    # 数据错误：偏差标注提及数据问题
    if any(k in note for k in ("数据", "行情异常", "价格错误", "停牌")):
        return "data_error"
    # 执行违规：偏离计划区间 / 追价 / 超仓
    if "above_range" in avp or "below_range" in avp or any(k in note for k in ("追价", "超仓", "偏离计划")):
        return "execution"
    # 流动性损失：滑点/无法退出
    if any(k in note for k in ("滑点", "无法退出", "跌停", "流动性")):
        return "liquidity"
    # 亏损但按规则执行 -> 规则内亏损
    if pnl is not None and float(pnl) < 0:
        return "rule_loss"
    # 盈利记录默认规则内
    return "rule_loss"


def error_report(conn: sqlite3.Connection) -> dict:
    """错误分类汇总：各类 n/金额/占比 + 改进建议。"""
    rows = conn.execute(
        """SELECT tr.*, tp.symbol FROM trade_records tr
           JOIN trade_plans tp ON tr.plan_id = tp.id"""
    ).fetchall()
    if not rows:
        return {"ok": False, "note": "无交易记录", "categories": {}}
    recs = [dict(r) for r in rows]
    by_cat: dict[str, list[float]] = {c: [] for c in CATEGORIES}
    for r in recs:
        cat = classify(r)
        pnl = float(r["pnl"]) if r["pnl"] is not None else 0.0
        by_cat.setdefault(cat, []).append(pnl)
    total = sum(sum(v) for v in by_cat.values())
    out = {}
    for cat, pnls in by_cat.items():
        if not pnls:
            continue
        n = len(pnls)
        out[cat] = {
            "label": CATEGORY_LABELS.get(cat, cat),
            "n": n,
            "total_pnl": round(sum(pnls), 2),
            "pct_of_n": round(n / len(recs), 3),
            "suggestion": SUGGESTIONS.get(cat, ""),
        }
    # 按金额排序
    ordered = sorted(out.items(), key=lambda x: x[1]["total_pnl"])
    return {
        "ok": True,
        "n": len(recs),
        "total_pnl": round(total, 2),
        "categories": dict(ordered),
    }
