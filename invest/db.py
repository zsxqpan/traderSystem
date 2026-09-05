"""SQLite 数据库：连接、Schema 初始化与版本迁移。

约定：
- 使用 PRAGMA user_version 做 schema 版本管理；
- 所有表结构变更时递增 SCHEMA_VERSION 并补充迁移逻辑；
- 连接默认 WAL 模式，row_factory 为 sqlite3.Row。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 18

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

-- 涨停/炸板池个股明细（2026-08-20：东财 push2ex，盘中实时，连板梯队/涨停龙头）
CREATE TABLE IF NOT EXISTS limit_up_pool (
    date            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    name            TEXT,
    lianban         INTEGER DEFAULT 0,
    first_seal_time TEXT,
    seal_amount     REAL,
    zhaban          INTEGER DEFAULT 0,
    src             TEXT DEFAULT 'eastmoney',
    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_lup_date_lianban ON limit_up_pool(date, lianban);

-- 行业板块主力资金（2026-08-20：东财 clist 行业资金流，主力净流入）
CREATE TABLE IF NOT EXISTS sector_fund_flow (
    date          TEXT NOT NULL,
    industry      TEXT NOT NULL,
    main_net      REAL,
    main_net_pct  REAL,
    src           TEXT DEFAULT 'eastmoney',
    PRIMARY KEY (date, industry)
);
CREATE INDEX IF NOT EXISTS idx_sff_date_net ON sector_fund_flow(date, main_net);

CREATE TABLE IF NOT EXISTS quant_valuation (
    run_date TEXT NOT NULL,
    obj      TEXT NOT NULL,
    pe_pct   REAL,
    pb_pct   REAL,
    crowding REAL,
    crowding_state TEXT DEFAULT '',
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
    pb       REAL,
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

-- 可靠任务执行账本：一个计划槽位只保留一条最新状态；job_runs 继续作为兼容事件流。
CREATE TABLE IF NOT EXISTS job_executions (
    job             TEXT NOT NULL,
    scheduled_date  TEXT NOT NULL,
    run_slot        TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 0,
    detail          TEXT DEFAULT '',
    artifact        TEXT DEFAULT '',
    channel_results TEXT DEFAULT '{}',
    started_at      TEXT,
    lease_expires_at TEXT,
    lease_owner     TEXT,
    finished_at     TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (job, scheduled_date, run_slot)
);
CREATE INDEX IF NOT EXISTS idx_job_executions_status_date
    ON job_executions(status, scheduled_date);

-- 逐通道持久投递回执。sending 在进程崩溃恢复时转 uncertain，禁止盲目重发。
CREATE TABLE IF NOT EXISTS delivery_receipts (
    job             TEXT NOT NULL,
    scheduled_date  TEXT NOT NULL,
    run_slot        TEXT NOT NULL,
    message_kind    TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    channel         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempt         INTEGER NOT NULL DEFAULT 0,
    detail          TEXT DEFAULT '',
    started_at      TEXT,
    succeeded_at    TEXT,
    updated_at      TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (job, scheduled_date, run_slot, message_kind, message_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_delivery_receipts_status
    ON delivery_receipts(status, scheduled_date);

CREATE TABLE IF NOT EXISTS llm_usage (
    date   TEXT NOT NULL,
    job    TEXT NOT NULL,
    tokens INTEGER DEFAULT 0,
    cost   REAL DEFAULT 0,
    PRIMARY KEY (date, job)
);

-- ============ PIT 化（2026-08-15，TODO 2.1） ============
-- 数据溯源：最小可追溯主键（as_of_time/object_id/reference_id/cycle/data_version/rule_version）
CREATE TABLE IF NOT EXISTS data_provenance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_time    TEXT NOT NULL,          -- 数据时点（YYYY-MM-DD HH:MM:SS）
    object_id     TEXT NOT NULL,          -- 对象（symbol/industry/index）
    object_type   TEXT DEFAULT '',        -- stock/industry/index/macro
    reference_id  TEXT DEFAULT '',        -- 关联（plan_id/viewpoint_id/task 名）
    cycle         TEXT DEFAULT '',        -- 周期（short/mid/long/波段/配置）
    data_version  TEXT DEFAULT '',        -- 数据版本（源/口径）
    rule_version  TEXT DEFAULT '',        -- 规则版本（因子/评级版本）
    note          TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_provenance_obj_time ON data_provenance(object_id, as_of_time);

-- 机会卡片（2026-08-15，TODO 1.3）
CREATE TABLE IF NOT EXISTS cards (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol         TEXT NOT NULL,
    level          TEXT DEFAULT 'B',        -- S/A/B/C
    cycle          TEXT DEFAULT 'short',    -- short/mid/long
    spread_type    TEXT DEFAULT '',         -- 主价差类型
    spread_value   TEXT DEFAULT '',         -- 主价差当前值（文本，含单位）
    thesis         TEXT DEFAULT '',         -- 三句话验证
    falsify        TEXT DEFAULT '',         -- 证伪条件
    entry_range    TEXT DEFAULT '',         -- 入场区间（如 "10.0,10.5"）
    stop_loss      REAL,
    target         REAL,
    status         TEXT DEFAULT 'candidate',-- candidate/locked/review/downgraded/void
    review_note    TEXT DEFAULT '',
    created_at     TEXT DEFAULT (datetime('now','localtime')),
    updated_at     TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_cards_symbol ON cards(symbol);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);

-- 规则版本管理（2026-08-15，TODO 2.6 / v3 3.3）
CREATE TABLE IF NOT EXISTS rule_versions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name         TEXT NOT NULL,          -- rating_position_map / indicators.strength / ...
    version           TEXT NOT NULL,          -- 版本号（如 v3.1）
    params_json       TEXT NOT NULL,          -- 该规则完整参数快照
    effective_date    TEXT,                   -- 生效日
    change_reason     TEXT DEFAULT '',
    validation_sample TEXT DEFAULT '',        -- 验证样本（回测区间/样本外区间）
    rollback_condition TEXT DEFAULT '',       -- 回滚条件
    status            TEXT DEFAULT 'active',  -- active / frozen / rolled_back
    created_at        TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_rule_versions_name ON rule_versions(rule_name, status);

-- 候选/否决/未执行机会全量留存（防选择偏差，v3 15.1）
CREATE TABLE IF NOT EXISTS candidate_decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    decision   TEXT NOT NULL,             -- add / reject / skip / remove
    symbol     TEXT NOT NULL,
    level      TEXT DEFAULT '',
    industry   TEXT DEFAULT '',
    reason     TEXT DEFAULT '',
    decided_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON candidate_decisions(symbol);

-- 历史行业归属/成分/ST 状态快照（[A]10，2026-08-15）
-- 每个交易日收盘后把「标的→行业（手工映射+候选池）/ST 状态」落库，
-- 供任意历史时点回溯成分与 ST 状态（数据源成本评估后确定回填范围）。
CREATE TABLE IF NOT EXISTS stock_universe_history (
    date      TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    industry  TEXT DEFAULT '',
    is_st     INTEGER DEFAULT 0,
    src       TEXT DEFAULT 'manual',
    PRIMARY KEY (date, symbol)
);

-- ============ 雪球大V画像与观点（2026-08-23，轻量子 skill big-v-monitor） ============
CREATE TABLE IF NOT EXISTS big_v_profile (
    id          TEXT PRIMARY KEY,          -- slug，如 xq_duanyp
    name        TEXT NOT NULL,             -- 显示名
    platform    TEXT NOT NULL DEFAULT 'xueqiu',
    xueqiu_id   TEXT,                      -- 雪球用户 ID
    homepage    TEXT,                      -- 主页 URL
    style       TEXT,                      -- 风格标签：价投/成长/游资/宏观/量化/技术/趋势
    strengths   TEXT,                      -- 擅长方向（行业/赛道）
    win_rate    TEXT,                      -- 自述/公开胜率（文本，注明口径）
    track_record TEXT,                     -- 历史战绩/里程碑（文本或 JSON）
    source_links TEXT,                     -- 公开资料链接（JSON 数组文本）
    notes       TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS big_v_opinion (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id   TEXT NOT NULL REFERENCES big_v_profile(id),
    opinion_date TEXT NOT NULL,            -- 观点发表日
    symbol       TEXT,                     -- 涉及标的（空=大盘/行业）
    topic        TEXT,
    view         TEXT NOT NULL,            -- 观点内容
    bias         TEXT,                     -- bullish / bearish / neutral
    confidence   REAL,                     -- 0-1，可空
    url          TEXT,                     -- 原文链接
    collected_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_big_v_opinion_profile ON big_v_opinion(profile_id, opinion_date);

-- ============ 候选池杀猪盘预警（2026-08-23，d31_pool_trap_alerts 定时扫描） ============
CREATE TABLE IF NOT EXISTS pool_trap_alerts (
    date           TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    level          TEXT NOT NULL,           -- 🟢 / 🟡 / 🟠 / 🔴
    trap_score     REAL,                    -- 反向分，越高越安全
    signals_hit    TEXT,                    -- JSON 数组 [{id,name,evidence,severity}]
    recommendation TEXT,
    src            TEXT NOT NULL DEFAULT 'trap_scan',
    PRIMARY KEY (date, symbol, src)
);

-- ============ 飞书对话历史（2026-08-24，run_chat 多轮上下文记忆） ============
CREATE TABLE IF NOT EXISTS chat_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL,
    sender_id  TEXT NOT NULL DEFAULT '',    -- 群聊按发送者隔离（2026-08-28）
    role       TEXT NOT NULL,               -- user / assistant
    content    TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_chat_history_chat ON chat_history(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_chat_history_chat_sender ON chat_history(chat_id, sender_id, id);

-- ============ 中期证据驾驶舱（2026-08-28） ============
CREATE TABLE IF NOT EXISTS fact_cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    obj_type        TEXT NOT NULL,             -- industry / stock
    obj             TEXT NOT NULL,
    as_of           TEXT NOT NULL,             -- YYYY-MM-DD
    data_version    TEXT NOT NULL DEFAULT '',
    rule_version    TEXT NOT NULL DEFAULT '',
    dimensions_json TEXT NOT NULL DEFAULT '{}',
    missing_json    TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (obj_type, obj, as_of)
);
CREATE INDEX IF NOT EXISTS idx_fact_cards_as_of ON fact_cards(as_of, obj_type);

CREATE TABLE IF NOT EXISTS fact_evidence (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id   TEXT NOT NULL UNIQUE,        -- EVID-YYYYMMDD-0001
    card_id       INTEGER NOT NULL,
    kind          TEXT NOT NULL,               -- dimension / news / announcement / sentiment
    source        TEXT NOT NULL DEFAULT '',
    url           TEXT DEFAULT '',
    published_at  TEXT DEFAULT '',
    fetched_at    TEXT DEFAULT '',
    as_of         TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    payload_json  TEXT DEFAULT '{}',
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (card_id) REFERENCES fact_cards(id)
);
CREATE INDEX IF NOT EXISTS idx_fact_evidence_card ON fact_evidence(card_id);
CREATE INDEX IF NOT EXISTS idx_fact_evidence_as_of ON fact_evidence(as_of, evidence_id);

CREATE TABLE IF NOT EXISTS comparison_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of          TEXT NOT NULL,
    peer_set_json  TEXT NOT NULL,              -- 人工选择的比较组
    conclusion     TEXT NOT NULL DEFAULT '',
    notes          TEXT DEFAULT '',
    data_version   TEXT DEFAULT '',
    rule_version   TEXT DEFAULT '',
    created_at     TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_comparison_as_of ON comparison_records(as_of);
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
    cols3 = [r["name"] for r in conn.execute("PRAGMA table_info(quant_strength)")]
    for col in ("rs5", "rs10", "rs20"):
        if col not in cols3:
            conn.execute(f"ALTER TABLE quant_strength ADD COLUMN {col} REAL")
    cols_val = [r["name"] for r in conn.execute("PRAGMA table_info(quant_valuation)")]
    if "crowding_state" not in cols_val:
        conn.execute("ALTER TABLE quant_valuation ADD COLUMN crowding_state TEXT DEFAULT ''")
    # 行业估值支持 PB（[A]1）：industry_valuation 增加 pb 列（乐咕乐股/东财行业估值）
    cols_ind = [r["name"] for r in conn.execute("PRAGMA table_info(industry_valuation)")]
    if "pb" not in cols_ind:
        conn.execute("ALTER TABLE industry_valuation ADD COLUMN pb REAL")
    cols_jobs = [r["name"] for r in conn.execute("PRAGMA table_info(job_executions)")]
    if "lease_expires_at" not in cols_jobs:
        conn.execute("ALTER TABLE job_executions ADD COLUMN lease_expires_at TEXT")
    if "lease_owner" not in cols_jobs:
        conn.execute("ALTER TABLE job_executions ADD COLUMN lease_owner TEXT")
    receipt_cols = [r["name"] for r in conn.execute("PRAGMA table_info(delivery_receipts)")]
    if "message_kind" not in receipt_cols or "message_id" not in receipt_cols:
        conn.execute("ALTER TABLE delivery_receipts RENAME TO delivery_receipts_v15")
        conn.execute(
            """CREATE TABLE delivery_receipts (
                   job TEXT NOT NULL,
                   scheduled_date TEXT NOT NULL,
                   run_slot TEXT NOT NULL,
                   message_kind TEXT NOT NULL,
                   message_id TEXT NOT NULL,
                   channel TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'pending',
                   attempt INTEGER NOT NULL DEFAULT 0,
                   detail TEXT DEFAULT '',
                   started_at TEXT,
                   succeeded_at TEXT,
                   updated_at TEXT DEFAULT (datetime('now','localtime')),
                   PRIMARY KEY (
                       job, scheduled_date, run_slot, message_kind, message_id, channel
                   )
               )"""
        )
        conn.execute(
            """INSERT INTO delivery_receipts(
                   job, scheduled_date, run_slot, message_kind, message_id,
                   channel, status, attempt, detail, started_at, succeeded_at, updated_at
               )
               SELECT job, scheduled_date, run_slot,
                      CASE WHEN job='pool_trap_scan' THEN 'alert' ELSE 'report' END,
                      CASE job
                          WHEN 'auction' THEN 'a7_auction'
                          WHEN 'morning_brief' THEN 'a0_premarket'
                          WHEN 'weekend' THEN 'a4_weekly'
                          WHEN 'monthly' THEN 'a5_monthly'
                          WHEN 'yearly' THEN 'a6_yearly'
                          WHEN 'evening_report' THEN 'a3_daily'
                          WHEN 'pool_trap_scan' THEN 'd31_pool_trap_alerts'
                          ELSE job
                      END,
                      channel, status, attempt, detail, started_at, succeeded_at, updated_at
               FROM delivery_receipts_v15"""
        )
        conn.execute("DROP TABLE delivery_receipts_v15")
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_delivery_receipts_status
               ON delivery_receipts(status, scheduled_date)"""
        )
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
    chat_cols = [r["name"] for r in conn.execute("PRAGMA table_info(chat_history)")]
    if chat_cols and "sender_id" not in chat_cols:
        conn.execute("ALTER TABLE chat_history ADD COLUMN sender_id TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_history_chat_sender "
        "ON chat_history(chat_id, sender_id, id)"
    )


def init_db(db_path: str | Path) -> None:
    """初始化 Schema；若版本落后则升级。"""
    conn = connect(db_path)
    try:
        # chat_history 是旧库已存在的表：先补 sender_id 列再跑 SCHEMA_SQL
        # （SCHEMA_SQL 中 idx_chat_history_chat_sender 引用该列，旧库直接
        #  executescript 会报 "no such column: sender_id"；新库无此表时跳过，
        #  由 SCHEMA_SQL 建全表）。幂等：_migrate 中同样的 ALTER 无副作用。
        chat_cols = [r["name"] for r in conn.execute("PRAGMA table_info(chat_history)")]
        if chat_cols and "sender_id" not in chat_cols:
            conn.execute("ALTER TABLE chat_history ADD COLUMN sender_id TEXT NOT NULL DEFAULT ''")
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current < 4:
            # v4 数据修复：板块/龙虎榜日期统一为 YYYY-MM-DD 并去重
            # （历史曾混写 20260804 与 2026-08-04 两种格式，同日两行数值相同）
            # 1) 先删掉已有 dashed 对应行的 compact 重复（否则 UPDATE 会撞主键）
            conn.execute(
                """DELETE FROM industry_bars WHERE date NOT LIKE '%-%' AND EXISTS (
                     SELECT 1 FROM industry_bars d2
                     WHERE d2.date LIKE '%-%'
                       AND d2.industry = industry_bars.industry
                       AND d2.src = industry_bars.src
                       AND REPLACE(d2.date,'-','') = industry_bars.date
                   )"""
            )
            # 2) 剩余 compact 转 dashed
            conn.execute(
                """UPDATE industry_bars SET date =
                     substr(date,1,4)||'-'||substr(date,5,2)||'-'||substr(date,7,2)
                   WHERE date NOT LIKE '%-%'"""
            )
            # 3) 兜底去重（幂等）
            conn.execute(
                """DELETE FROM industry_bars WHERE rowid NOT IN (
                     SELECT MIN(rowid) FROM industry_bars GROUP BY industry, date, src
                   )"""
            )
            conn.execute(
                """UPDATE dragon_tiger SET date =
                     substr(date,1,4)||'-'||substr(date,5,2)||'-'||substr(date,7,2)
                   WHERE date NOT LIKE '%-%'"""
            )
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