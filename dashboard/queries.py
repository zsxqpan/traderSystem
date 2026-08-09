"""仪表盘数据查询（只读）。"""
from __future__ import annotations

import pandas as pd

from invest.db import connect


def _read(db_path: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = connect(db_path)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def load_strength(db: str) -> pd.DataFrame:
    return _read(db, "SELECT obj, rs, momentum, trend_stage FROM quant_strength WHERE period='short' AND obj_type='industry' AND run_date = (SELECT MAX(run_date) FROM quant_strength WHERE period='short' AND obj_type='industry') ORDER BY rs DESC")


def load_weekly(db: str) -> pd.DataFrame:
    return _read(db, "SELECT obj, rs, momentum, trend_stage FROM quant_strength WHERE period='mid' AND obj_type='industry' AND run_date = (SELECT MAX(run_date) FROM quant_strength WHERE period='mid' AND obj_type='industry') ORDER BY rs DESC")


def load_temperature(db: str) -> pd.DataFrame:
    return _read(db, "SELECT run_date, profit_effect, score FROM quant_temperature ORDER BY run_date DESC LIMIT 1")


def load_temperature_history(db: str, limit: int = 60) -> pd.DataFrame:
    """市场温度历史序列（用于趋势图）。"""
    return _read(
        db,
        "SELECT run_date, score, profit_effect, limit_up_count FROM quant_temperature ORDER BY run_date DESC LIMIT ?",
        (limit,),
    ).iloc[::-1].reset_index(drop=True)


def load_latest_movers(db: str) -> pd.DataFrame:
    """最新行业交易日板块涨跌幅与成交额（用于热力图/树图）。"""
    return _read(
        db,
        """WITH ranked AS (
             SELECT industry, close, amount,
                    ROW_NUMBER() OVER (PARTITION BY industry ORDER BY REPLACE(date,'-','') DESC) rn
             FROM industry_bars
           )
           SELECT a.industry,
                  (a.close/b.close - 1) AS pct,
                  a.amount
           FROM ranked a JOIN ranked b ON a.industry=b.industry AND b.rn=2
           WHERE a.rn=1
           ORDER BY pct DESC"""
    )


def load_crowding_vs_strength(db: str) -> pd.DataFrame:
    """拥挤度 × 相对强度散点数据（最新快照）。"""
    return _read(
        db,
        """SELECT s.obj, s.rs, s.trend_stage, v.crowding
           FROM quant_strength s
           JOIN quant_valuation v ON v.obj = s.obj
           WHERE s.period='short' AND s.obj_type='industry'
             AND s.run_date = (SELECT MAX(run_date) FROM quant_strength WHERE period='short' AND obj_type='industry')
             AND v.run_date = (SELECT MAX(run_date) FROM quant_valuation)
           ORDER BY s.rs DESC"""
    )


def load_data_health(db: str) -> pd.DataFrame:
    """各行情表最新日期与滞后天数（数据健康横幅）。"""
    sql = """
        SELECT 'industry_bars' AS tbl, MAX(date) AS max_date FROM industry_bars
        UNION ALL SELECT 'index_bars', MAX(date) FROM index_bars
        UNION ALL SELECT 'daily_bars', MAX(date) FROM daily_bars
        UNION ALL SELECT 'market_emotion', MAX(date) FROM market_emotion
        UNION ALL SELECT 'industry_valuation', MAX(date) FROM industry_valuation
        UNION ALL SELECT 'dragon_tiger', MAX(date) FROM dragon_tiger
        UNION ALL SELECT 'macro_series', MAX(date) FROM macro_series
    """
    df = _read(db, sql)
    if df.empty:
        return df
    today = pd.Timestamp.today().normalize()
    df["max_date"] = pd.to_datetime(df["max_date"], format="mixed", errors="coerce")
    df["lag_days"] = (today - df["max_date"]).dt.days
    df["status"] = df["lag_days"].apply(
        lambda d: "正常" if d <= 1 else ("偏旧" if d <= 5 else "过期")
    )
    return df


def load_rotation(db: str) -> pd.DataFrame:
    return _read(db, "SELECT industry, rank, lead_lag, turnover_share FROM quant_rotation WHERE run_date = (SELECT MAX(run_date) FROM quant_rotation) ORDER BY rank")


def load_linkage(db: str) -> pd.DataFrame:
    return _read(db, "SELECT a, b, corr, lead FROM quant_linkage WHERE run_date = (SELECT MAX(run_date) FROM quant_linkage) ORDER BY corr DESC")


def load_capital(db: str) -> pd.DataFrame:
    return _read(db, "SELECT obj, fund_type, style, confidence FROM quant_capital q WHERE run_date = (SELECT MAX(run_date) FROM quant_capital q2 WHERE q2.obj_type = q.obj_type) ORDER BY confidence DESC")


def load_crowding(db: str) -> pd.DataFrame:
    return _read(db, "SELECT obj, crowding FROM quant_valuation WHERE run_date = (SELECT MAX(run_date) FROM quant_valuation) ORDER BY crowding DESC")


def load_macro(db: str) -> pd.DataFrame:
    return _read(db, "SELECT date, indicator, value FROM quant_macro ORDER BY date DESC, indicator")


def load_viewpoints(db: str, status: str | None = None, limit: int = 100) -> pd.DataFrame:
    sql = "SELECT id, source, obj, conclusion, period_tag, confidence, status, valid_until, created_at FROM viewpoints"
    args: list = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return _read(db, sql, tuple(args))


def load_accuracy(db: str) -> pd.DataFrame:
    from invest.viewpoints.accuracy import accuracy_stats
    conn = connect(db)
    try:
        df = pd.DataFrame(accuracy_stats(conn, group_by="source"))
    finally:
        conn.close()
    if df.empty:
        df = pd.DataFrame(columns=["group", "verified", "invalidated", "accuracy"])
    return df


def load_pool(db: str) -> pd.DataFrame:
    return _read(db, "SELECT symbol, level, reason, falsify_condition, in_date FROM candidate_pool WHERE out_date IS NULL ORDER BY level, in_date")


def load_ratings(db: str) -> pd.DataFrame:
    return _read(db, "SELECT date, kind, value FROM ratings ORDER BY date DESC")


def load_plans(db: str) -> pd.DataFrame:
    return _read(db, "SELECT id, symbol, buy_range, target_position, stop_loss, status FROM trade_plans ORDER BY created_at DESC")


def load_records(db: str) -> pd.DataFrame:
    return _read(db, "SELECT plan_id, action, price, qty, actual_vs_plan, deviation_note, created_at FROM trade_records ORDER BY created_at DESC LIMIT 100")


def load_backtests(db: str) -> pd.DataFrame:
    return _read(db, "SELECT id, rule_type, dataset_range, created_at FROM backtest_runs ORDER BY id DESC")


def load_jobs(db: str, limit: int = 20) -> pd.DataFrame:
    return _read(db, "SELECT job, status, started_at, finished_at FROM job_runs ORDER BY id DESC LIMIT ?", (limit,))


def load_coverage(db: str) -> pd.DataFrame:
    conn = connect(db)
    try:
        rows = conn.execute(
            """SELECT 'industry_bars' AS tbl, COUNT(*) AS rows, MIN(date) AS min_date, MAX(date) AS max_date FROM industry_bars
               UNION ALL SELECT 'index_bars', COUNT(*), MIN(date), MAX(date) FROM index_bars
               UNION ALL SELECT 'daily_bars', COUNT(*), MIN(date), MAX(date) FROM daily_bars
               UNION ALL SELECT 'dragon_tiger', COUNT(*), MIN(date), MAX(date) FROM dragon_tiger
               UNION ALL SELECT 'macro_series', COUNT(*), MIN(date), MAX(date) FROM macro_series"""
        ).fetchall()
        return pd.DataFrame([dict(r) for r in rows])
    finally:
        conn.close()