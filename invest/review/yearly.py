"""年度复盘：体系规则有效性（回测结论 vs 实盘），输出修订建议模板。"""
from __future__ import annotations

import json
import sqlite3


def yearly_review(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT id, rule_type, params_json, metrics_json, dataset_range, created_at FROM backtest_runs ORDER BY id DESC LIMIT 6"
    ).fetchall()
    rules = []
    for r in rows:
        try:
            metrics = json.loads(r["metrics_json"] or "[]")
        except (ValueError, TypeError):
            metrics = []
        rules.append({
            "rule_type": r["rule_type"],
            "dataset": r["dataset_range"],
            "created_at": r["created_at"],
            "summary": metrics[:3],
        })
    return {
        "backtest_summary": rules,
        "suggestions": [
            "1. 对照实盘交易记录评估各规则命中情况（个股数据接入后启用）",
            "2. 修订评级-仓位映射参数前须先完成仓位规则回测",
            "3. 确认市场温度区间阈值（40/60/80）是否随样本更新",
        ],
    }