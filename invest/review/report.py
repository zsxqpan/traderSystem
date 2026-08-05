"""复盘报告落库。"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

from invest.data.storage import upsert_df


def save_report(conn: sqlite3.Connection, period: str, report_type: str, content: dict) -> None:
    upsert_df(conn, "review_reports", pd.DataFrame([{
        "period": period,
        "report_type": report_type,
        "content_json": json.dumps(content, ensure_ascii=False),
        "created_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    }]))