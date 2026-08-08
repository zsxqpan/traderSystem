"""数据库写入：DataFrame upsert（INSERT OR REPLACE）。"""
from __future__ import annotations

import sqlite3

import pandas as pd


def upsert_df(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    """按表主键写入/替换，返回写入行数。df 列名必须与表列一致。"""
    if df is None or df.empty:
        return 0
    cols = [str(c) for c in df.columns]
    placeholders = ",".join("?" * len(cols))
    sql = (
        f"INSERT OR REPLACE INTO {table} "
        f"({','.join(cols)}) VALUES ({placeholders})"
    )
    rows = [
        tuple(None if pd.isna(v) else v for v in r)
        for r in df[cols].itertuples(index=False, name=None)
    ]
    with conn:
        conn.executemany(sql, rows)
    return len(rows)