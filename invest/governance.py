"""权重治理与规则版本管理（TODO 2.6 / v3 3.3，2026-08-15）。

- 四套权重冻结：把当前 config.yaml 的 rating_position_map + indicators 快照
  为基准版本（frozen），不再月度 ±5pp 随意调；
- 规则版本管理：version/effective_date/change_reason/validation_sample/
  rollback_condition 全字段留痕，支持冻结/激活/回滚；
- 季度样本外评估：榜单 top N 方向 N 日超额收益（复用 backtest forward_excess），
  含重叠样本折算与多重检验提示。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pandas as pd

from invest.config import load_yaml_config

# 四套权重（规则名）：评级-仓位映射 + 四组指标参数
WEIGHT_RULES = (
    "rating_position_map",   # 评级-仓位映射（第一套）
    "indicators.strength",   # 短线轨指标
    "indicators.weekly_strength",  # 中线轨指标
    "indicators.crowding",   # 拥挤度阈值
)


def _rule_params(rule_name: str, config: dict | None = None) -> dict:
    """从 config 提取指定规则的参数快照。"""
    config = config or load_yaml_config()
    if rule_name == "rating_position_map":
        return config.get("rating_position_map", {})
    section = rule_name.split(".", 1)
    if len(section) == 2 and section[0] == "indicators":
        return (config.get("indicators", {}) or {}).get(section[1], {})
    return {}


def freeze_weights(
    conn: sqlite3.Connection,
    version: str,
    change_reason: str = "",
    validation_sample: str = "",
    rollback_condition: str = "",
) -> list[dict]:
    """冻结四套权重为基准版本（frozen），返回写入记录。

    已存在同规则 active/frozen 版本时，先标记为 rolled_back（历史化）。
    """
    written: list[dict] = []
    config = load_yaml_config()
    with conn:
        for rule in WEIGHT_RULES:
            params = _rule_params(rule, config)
            if not params:
                continue
            # 历史化同规则所有旧版本（active+frozen 均降为 rolled_back，只留最新基准）
            conn.execute(
                "UPDATE rule_versions SET status='rolled_back' WHERE rule_name=? AND status IN ('active','frozen')",
                (rule,),
            )
            cur = conn.execute(
                """INSERT INTO rule_versions(rule_name, version, params_json, effective_date,
                                             change_reason, validation_sample, rollback_condition, status)
                   VALUES(?,?,?,date('now','localtime'),?,?,?, 'frozen')""",
                (rule, version, json.dumps(params, ensure_ascii=False),
                 change_reason, validation_sample, rollback_condition),
            )
            written.append({"rule": rule, "version": version, "id": cur.lastrowid, "status": "frozen"})
    return written


def current_versions(conn: sqlite3.Connection, status: str | None = "frozen") -> list[dict]:
    """查询版本记录（默认 frozen 基准）。"""
    sql = "SELECT * FROM rule_versions"
    args: tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    sql += " ORDER BY rule_name, id DESC"
    rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def rollback(
    conn: sqlite3.Connection,
    rule_name: str,
    version: str,
    reason: str = "",
) -> dict:
    """回滚：把指定版本重新激活为 active（其他同规则版本历史化）。"""
    row = conn.execute(
        "SELECT * FROM rule_versions WHERE rule_name=? AND version=? ORDER BY id DESC LIMIT 1",
        (rule_name, version),
    ).fetchone()
    if row is None:
        raise ValueError(f"规则 {rule_name} 版本 {version} 不存在")
    with conn:
        conn.execute(
            "UPDATE rule_versions SET status='rolled_back' WHERE rule_name=? AND status='active'",
            (rule_name,),
        )
        conn.execute(
            """UPDATE rule_versions SET status='active', change_reason = change_reason || ' | 回滚:' || ?
               WHERE id=?""",
            (reason, row["id"]),
        )
    return {"rule": rule_name, "version": version, "status": "active"}


def params_for(conn: sqlite3.Connection, rule_name: str, status: str = "active") -> dict:
    """取指定规则当前生效参数（active 优先，无则 frozen 最新）。"""
    for st in (status, "frozen"):
        row = conn.execute(
            """SELECT params_json FROM rule_versions
               WHERE rule_name=? AND status=? ORDER BY id DESC LIMIT 1""",
            (rule_name, st),
        ).fetchone()
        if row:
            try:
                return json.loads(row["params_json"])
            except (ValueError, TypeError):
                return {}
    return {}


def quarterly_oos_eval(
    conn: sqlite3.Connection,
    start_date: str | None = None,
    end_date: str | None = None,
    top_n: int = 5,
    horizon: int = 10,
    min_n: int | None = None,
) -> dict:
    """季度样本外评估：quant_strength 榜单 top N 方向 N 日超额收益。

    用 industry_bars 计算：每期 top N 行业的 10 日前向超额（相对 000300），
    汇总 n/mean/win_rate；含重叠样本提示（相邻期样本重叠，非独立检验）。
    """
    min_n = min_n if min_n is not None else min(top_n, 3)  # 每期 top_n 只可能 <= top_n 个样本
    ind = pd.read_sql_query(
        "SELECT date, industry, close FROM industry_bars ORDER BY date", conn,
    )
    idx = pd.read_sql_query(
        "SELECT date, close FROM index_bars WHERE index_code='000300' ORDER BY date", conn,
    )
    if ind.empty or idx.empty:
        return {"ok": False, "note": "数据不足（industry_bars/index_bars 为空）"}
    ind["date"] = pd.to_datetime(ind["date"], format="mixed", errors="coerce")
    idx["date"] = pd.to_datetime(idx["date"], format="mixed", errors="coerce")
    closes = ind.pivot_table(index="date", columns="industry", values="close")
    bench = idx.set_index("date")["close"]

    # 前向超额：每行业 close.shift(-h)/close-1 - 基准同窗口（DataFrame 逐列计算）
    bench_fwd = bench.shift(-horizon) / bench - 1
    fwd = closes.shift(-horizon) / closes - 1
    fwd = fwd.sub(bench_fwd, axis=0)  # 逐行减去基准前向收益

    # 每期取 RS 榜 top_n（quant_strength short 轨）
    rows = conn.execute(
        """SELECT run_date, obj FROM quant_strength
           WHERE period='short' AND obj_type='industry'
           ORDER BY run_date"""
    ).fetchall()
    scores: dict[str, list[str]] = {}
    for r in rows:
        scores.setdefault(r["run_date"], []).append(r["obj"])
    per_period: list[float] = []
    samples = 0
    for run_date, objs in scores.items():
        top = objs[:top_n]
        # 找该 run_date 之后最近的行情日
        ts = pd.Timestamp(run_date)
        avail = fwd.index[fwd.index >= ts]
        if len(avail) == 0:
            continue
        row_date = avail[0]
        vals = fwd.loc[row_date, [c for c in top if c in fwd.columns]].dropna()
        if len(vals) >= min_n:
            per_period.append(float(vals.mean()))
            samples += len(vals)
    if not per_period:
        return {
            "ok": False,
            "note": (
                f"无足够评估样本：quant_strength 历史太短（共 {len(scores)} 期，"
                f"需回溯 >{horizon} 个交易日才有前向收益）。"
                "请先用 scripts/backfill.py 回填历史量化数据后再评估。"
            ),
        }
    mean = sum(per_period) / len(per_period)
    win = sum(1 for v in per_period if v > 0) / len(per_period)
    return {
        "ok": True,
        "n_periods": len(per_period),
        "n_samples": samples,
        "mean_excess": round(mean, 5),
        "win_rate": round(win, 4),
        "horizon_days": horizon,
        "overlap_note": "相邻期榜单样本重叠，非独立检验；结论仅作参考（v3 8.5 滚动 IC 为准）",
    }
