"""多源交叉校验与数据源可信度评级。"""
from __future__ import annotations

import sqlite3

import pandas as pd

DEFAULT_TOLERANCES = {
    "open": 0.001, "high": 0.001, "low": 0.001, "close": 0.001,
    "volume": 0.01,
}


def cross_check(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    key: str = "date",
    cols: list[str] | None = None,
    tolerances: dict[str, float] | None = None,
) -> tuple[bool, dict]:
    """按 key 对齐两源数据，逐列比较相对误差。

    返回 (ok, report)；ok=False 表示存在超过容差的行。
    """
    tolerances = tolerances or DEFAULT_TOLERANCES
    if cols is None:
        cols = [c for c in ("open", "high", "low", "close", "volume")
                if c in df1.columns and c in df2.columns]
    if not cols:
        return False, {"rows": 0, "issues": ["两源无共同可比列"]}

    merged = df1[[key] + cols].merge(df2[[key] + cols], on=key, suffixes=("_a", "_b"))
    if merged.empty:
        return False, {"rows": 0, "issues": ["无共同日期可校验"]}

    issues = []
    for col in cols:
        a = pd.to_numeric(merged[f"{col}_a"], errors="coerce")
        b = pd.to_numeric(merged[f"{col}_b"], errors="coerce")
        valid = a.notna() & b.notna()
        if not valid.any():
            continue
        rel = (a[valid] - b[valid]).abs() / b[valid].abs().clip(lower=1e-9)
        tol = tolerances.get(col, 0.001)
        bad = rel > tol
        if bad.any():
            issues.append({
                "col": col,
                "bad_rows": int(bad.sum()),
                "max_rel": float(rel.max()),
            })
    ok = not issues
    return ok, {"rows": int(len(merged)), "issues": issues}


def update_credibility(conn: sqlite3.Connection, source: str, success: bool) -> None:
    """记录源调用结果并滚动更新可信度（0.1-1.0）。

    成功：可信度重置为 1.0，失败计数清零；
    失败：可信度 -0.1（下限 0.1），失败计数 +1。
    """
    with conn:
        if success:
            conn.execute(
                """INSERT INTO data_sources(name, credibility, failures, last_check)
                   VALUES(?, 1.0, 0, datetime('now','localtime'))
                   ON CONFLICT(name) DO UPDATE SET
                     credibility = 1.0, failures = 0, last_check = datetime('now','localtime')""",
                (source,),
            )
        else:
            conn.execute(
                """INSERT INTO data_sources(name, credibility, failures, last_check)
                   VALUES(?, 0.9, 1, datetime('now','localtime'))
                   ON CONFLICT(name) DO UPDATE SET
                     credibility = MAX(0.1, credibility - 0.1),
                     failures = failures + 1,
                     last_check = datetime('now','localtime')""",
                (source,),
            )