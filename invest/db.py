"""SQLite 数据库：连接、Schema 初始化与版本迁移。

约定：
- 使用 PRAGMA user_version 做 schema 版本管理；
- 所有表结构变更时递增 SCHEMA_VERSION 并补充迁移逻辑；
- 连接默认 WAL 模式，row_factory 为 sqlite3.Row。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA_SQL = """
-- ============ 行情 ============
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL,
    volume INTEGER, amount REAL,
    src    TEXT NOT NULL DEFAULT 'akshare',
    PRIMARY KEY (symbol, date, src)
);
CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_date ON daily_bars(symbol, date);

CREATE TABLE IF NOT EXISTS index_bars (
    index_code TEXT NOT NULL,
    date       TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER, amount REAL,
    src  TEXT NOT NULL DEFAULT 'akshare',
    PRIMARY KEY (index_code, date, src)
);
CREATE INDEX IF NOT EXISTS idx_index_bars_code_date ON index_bars(index_code, date);

CREATE TABLE IF NOT EXISTS industry_bars (
    industry TEXT NOT NULL,
    date     TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER, amount REAL,
    src  TEXT NOT NULL DEFAULT 'akshare',
    PRIMARY KEY (industry, date, src)
);
CREATE INDEX IF NOT EXISTS idx_industry_bars_ind_date ON industry_bars(industry, date);

-- ============ 资金与宏观 ============
CREATE TABLE IF NOT EXISTS dragon_tiger (
    date      TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    name      TEXT,
    seat_type TEXT,
    buy REAL, sell REAL, net REAL,
    src TEXT NOT NULL DEFAULT 'akshare',
    PRIMARY KEY (date, symbol, seat_type, src)
);

CREATE TABLE IF NOT EXISTS margin (
    date    TEXT PRIMARY KEY,
    balance REAL,
    buy REAL, repay REAL,
    src TEXT NOT NULL DEFAULT 'akshare'
);

CREATE TABLE IF NOT EXISTS macro_series (
    indicator TEXT NOT NULL,
    date      TEXT NOT NULL,
    value     REAL,
    unit      TEXT,
    src TEXT NOT NULL DEFAULT 'akshare',
    PRIMARY KEY (indicator, date, src)
);

CREATE TABLE IF NOT EXISTS industry_cycle (
    industry       TEXT PRIMARY KEY,
    phase          TEXT,
    key_indicators TEXT,
    notes          TEXT,
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS event_calendar (
    date       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    title      TEXT,
    target     TEXT,
    level      TEXT DEFAULT 'normal',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- ============ 定量结果（按 run_date 覆盖写） ============
CREATE TABLE IF NOT EXISTS quant_strength (
    run_date    TEXT NOT NULL,
    obj_type    TEXT NOT NULL,
    obj         TEXT NOT NULL,
    period      TEXT NOT NULL,
    rs          REAL,
    momentum    REAL,
    trend_stage TEXT,
    calc_version TEXT,
    PRIMARY KEY (run_date, obj_type, obj, period)
);

CREATE TABLE IF NOT EXISTS quant_rotation (
    run_date      TEXT NOT NULL,
    industry      TEXT NOT NULL,
    rank          INTEGER,
    lead_lag      TEXT,
    turnover_share REAL,
    PRIMARY KEY (run_date, industry)
);

CREATE TABLE IF NOT EXISTS quant_temperature (
    run_date        TEXT PRIMARY KEY,
    limit_up_count  INTEGER,
    max_lianban     INTEGER,
    zhaban_rate     REAL,
    profit_effect   REAL,
    score           REAL
);

CREATE TABLE IF NOT EXISTS quant_capital (
    run_date   TEXT NOT NULL,
    obj        TEXT NOT NULL,
    obj_type   TEXT,
    fund_type  TEXT,
    style      TEXT,
    confidence REAL,
    PRIMARY KEY (run_date, obj)
);

CREATE TABLE IF NOT EXISTS quant_linkage (
    run_date TEXT NOT NULL,
    a        TEXT NOT NULL,
    b        TEXT NOT NULL,
    corr     REAL,
    lead     TEXT,
    PRIMARY KEY (run_date, a, b)
);

CREATE TABLE IF NOT EXISTS quant_valuation (
    run_date TEXT NOT NULL,
    obj      TEXT NOT NULL,
    pe_pct   REAL,
    pb_pct   REAL,
    crowding REAL,
    PRIMARY KEY (run_date, obj)
);

CREATE TABLE IF NOT EXISTS quant_macro (
    date      TEXT NOT NULL,
    indicator TEXT NOT NULL,
    value     REAL,
    PRIMARY KEY (date, indicator)
);

CREATE TABLE IF NOT EXISTS market_emotion (
    date            TEXT PRIMARY KEY,
    limit_up_count  INTEGER,
    max_lianban     INTEGER,
    zhaban_count    INTEGER,
    zhaban_rate     REAL,
    src             TEXT DEFAULT 'akshare'
);

CREATE TABLE IF NOT EXISTS industry_valuation (
    date     TEXT NOT NULL,
    industry TEXT NOT NULL,
    pe       REAL,
    level    INTEGER,
    src      TEXT DEFAULT 'akshare',
    PRIMARY KEY (date, industry, level, src)
);

-- ============ 观点库 ============
CREATE TABLE IF NOT EXISTS viewpoints (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source           TEXT NOT NULL,
    obj_type         TEXT,
    obj              TEXT,
    attention_level  TEXT,
    conclusion       TEXT NOT NULL,
    period_tag       TEXT NOT NULL,
    valid_until      TEXT,
    confidence       REAL,
    data_credibility TEXT,
    evidence_json    TEXT,
    invalid_condition TEXT,
    status           TEXT DEFAULT 'active',
    review_note      TEXT,
    created_at       TEXT DEFAULT (datetime('now','localtime')),
    updated_at       TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_viewpoints_obj_status ON viewpoints(obj, status);
CREATE INDEX IF NOT EXISTS idx_viewpoints_expiry ON viewpoints(valid_until);

-- ============ 工单 ============
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    direction   TEXT,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    payload_json TEXT,
    status      TEXT DEFAULT 'created',
    deadline    TEXT,
    resolved_at TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- ============ 执行纪律 ============
CREATE TABLE IF NOT EXISTS candidate_pool (
    symbol              TEXT PRIMARY KEY,
    level               TEXT NOT NULL,
    industry            TEXT,
    reason              TEXT,
    target_value_range  TEXT,
    falsify_condition   TEXT,
    in_date             TEXT DEFAULT (date('now','localtime')),
    out_date            TEXT
);
CREATE INDEX IF NOT EXISTS idx_pool_level ON candidate_pool(level);

CREATE TABLE IF NOT EXISTS ratings (
    date       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    value      TEXT NOT NULL,
    basis_json TEXT,
    PRIMARY KEY (date, kind)
);

CREATE TABLE IF NOT EXISTS trade_plans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT NOT NULL,
    ref_viewpoint_id  INTEGER,
    buy_range         TEXT,
    target_position   REAL,
    stop_loss         REAL,
    take_profit       TEXT,
    invalid_condition TEXT,
    status            TEXT DEFAULT 'active',
    created_at        TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_plans_symbol_status ON trade_plans(symbol, status);

CREATE TABLE IF NOT EXISTS trade_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         INTEGER,
    action          TEXT NOT NULL,
    price           REAL,
    qty             INTEGER,
    actual_vs_plan  TEXT,
    deviation_note  TEXT,
    pnl             REAL,
    emotion_note    TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_records_plan ON trade_records(plan_id);

CREATE TABLE IF NOT EXISTS risk_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type  TEXT NOT NULL,
    params_json TEXT,
    enabled    INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type     TEXT NOT NULL,
    params_json   TEXT,
    metrics_json  TEXT,
    dataset_range TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS review_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period       TEXT NOT NULL,
    report_type  TEXT NOT NULL,
    content_json TEXT,
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);

-- ============ 运行支撑 ============
CREATE TABLE IF NOT EXISTS data_sources (
    name        TEXT PRIMARY KEY,
    credibility REAL DEFAULT 1.0,
    failures    INTEGER DEFAULT 0,
    last_check  TEXT
);

CREATE TABLE IF NOT EXISTS job_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  TEXT DEFAULT (datetime('now','localtime')),
    finished_at TEXT,
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS llm_usage (
    date   TEXT NOT NULL,
    job    TEXT NOT NULL,
    tokens INTEGER DEFAULT 0,
    cost   REAL DEFAULT 0,
    PRIMARY KEY (date, job)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开数据库连接（自动建父目录、WAL、Row 工厂）。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """旧库增量迁移（幂等）。"""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(quant_capital)")]
    if "obj_type" not in cols:
        conn.execute("ALTER TABLE quant_capital ADD COLUMN obj_type TEXT")
    cols2 = [r["name"] for r in conn.execute("PRAGMA table_info(candidate_pool)")]
    if "industry" not in cols2:
        conn.execute("ALTER TABLE candidate_pool ADD COLUMN industry TEXT")
    # dragon_tiger 历史 bug：榜单行不写 seat_type（NULL），SQLite 主键中
    # NULL!=NULL，导致每次采集重复插入。清理每个 (date,symbol) 仅保留一行；
    # 新数据由采集层统一写入非空 seat_type='list'（见 akshare_source.py）。
    conn.execute(
        """DELETE FROM dragon_tiger
           WHERE seat_type IS NULL AND rowid NOT IN (
               SELECT MIN(rowid) FROM dragon_tiger
               WHERE seat_type IS NULL GROUP BY date, symbol
           )"""
    )


def init_db(db_path: str | Path) -> None:
    """初始化 Schema；若版本落后则升级。"""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def table_names(db_path: str | Path) -> list[str]:
    """返回业务表名列表（排除 sqlite_* 内部表）。"""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r["name"] for r in rows]
    finally:
        conn.close()