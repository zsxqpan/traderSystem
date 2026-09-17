"""仪表盘数据查询（只读）。"""
from __future__ import annotations

import datetime as dt
import re

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


_SCATTER_SQL = """SELECT s.obj, s.rs, s.trend_stage, v.crowding, v.crowding_state,
                  (SELECT ts.signal_id FROM trade_signals ts
                   WHERE ts.session='daily' AND ts.signal_id LIKE 'quad_%'
                     AND ts.subject = s.obj
                     AND ts.date = (SELECT MAX(date) FROM trade_signals WHERE session='daily')
                   LIMIT 1) AS quad_id
           FROM quant_strength s
           JOIN quant_valuation v ON v.obj = s.obj
           WHERE s.period='short' AND s.obj_type='industry'
             AND s.run_date = (SELECT MAX(run_date) FROM quant_strength WHERE period='short' AND obj_type='industry')
             AND v.run_date = (SELECT MAX(run_date) FROM quant_valuation)
           ORDER BY s.rs DESC"""

_SCATTER_SQL_NO_SIG = """SELECT s.obj, s.rs, s.trend_stage, v.crowding, v.crowding_state,
                  CAST(NULL AS TEXT) AS quad_id
           FROM quant_strength s
           JOIN quant_valuation v ON v.obj = s.obj
           WHERE s.period='short' AND s.obj_type='industry'
             AND s.run_date = (SELECT MAX(run_date) FROM quant_strength WHERE period='short' AND obj_type='industry')
             AND v.run_date = (SELECT MAX(run_date) FROM quant_valuation)
           ORDER BY s.rs DESC"""


def load_crowding_vs_strength(db: str) -> pd.DataFrame:
    """拥挤度 × 相对强度散点数据（最新快照）。含 crowding_state 与当日 quad_id。缺表不炸。"""
    empty = pd.DataFrame(columns=["obj", "rs", "trend_stage", "crowding", "crowding_state", "quad_id"])
    try:
        return _read(db, _SCATTER_SQL)
    except Exception:
        try:
            return _read(db, _SCATTER_SQL_NO_SIG)
        except Exception:
            return empty


_SIGNAL_COLS = [
    "date", "session", "horizon", "layer", "severity",
    "signal_id", "name", "subject", "hint", "evidence",
]


def load_signals(
    db: str,
    *,
    asof=None,
    horizon: str | None = None,
    session: str | None = None,
    layer: str | None = None,
    severity: str | None = None,
    limit: int = 200,
    days: int | None = None,
) -> pd.DataFrame:
    """封装 list_signals → DataFrame。days 有值时取最近 N 个自然日。"""
    import datetime as dt

    from invest.signals.query import list_signals

    conn = connect(db)
    try:
        asof_d = asof
        if isinstance(asof_d, str) and asof_d:
            asof_d = dt.date.fromisoformat(asof_d[:10])
        date_from = None
        if days:
            if asof_d is None:
                try:
                    row = conn.execute("SELECT MAX(date) AS d FROM trade_signals").fetchone()
                    raw = row["d"] if row else None
                    asof_d = dt.date.fromisoformat(str(raw)[:10]) if raw else None
                except Exception:
                    return pd.DataFrame(columns=_SIGNAL_COLS)
            if asof_d is not None:
                date_from = asof_d - dt.timedelta(days=int(days) - 1)
        rows = list_signals(
            conn,
            asof_d,
            horizon=horizon,
            session=session,
            layer=layer,
            severity=severity,
            limit=limit,
            date_from=date_from,
        )
    except Exception:
        return pd.DataFrame(columns=_SIGNAL_COLS)
    finally:
        conn.close()
    if not rows:
        return pd.DataFrame(columns=_SIGNAL_COLS)
    df = pd.DataFrame(rows)
    for c in _SIGNAL_COLS:
        if c not in df.columns:
            df[c] = ""
    df["evidence"] = df["evidence"].map(
        lambda x: x if isinstance(x, str) else ("" if x is None else str(x))
    )
    return df[_SIGNAL_COLS]


def load_rotation_history(db: str) -> pd.DataFrame:
    """板块轮动排名历史（排名轨迹图）。"""
    return _read(
        db,
        "SELECT run_date, industry, rank, lead_lag, turnover_share FROM quant_rotation ORDER BY run_date, rank"
    )


def load_linkage_edges(db: str, threshold: float = 0.8, max_edges: int = 150) -> pd.DataFrame:
    """最新联动网络高相关边（按 corr 降序截断，避免图太密）。"""
    return _read(
        db,
        """SELECT a, b, corr, lead FROM quant_linkage
           WHERE run_date = (SELECT MAX(run_date) FROM quant_linkage) AND corr >= ?
           ORDER BY corr DESC LIMIT ?""",
        (threshold, max_edges),
    )


def load_style_history(db: str) -> pd.DataFrame:
    """行业资金风格占比历史（风格轮动时间线）。"""
    return _read(
        db,
        """SELECT run_date, style, COUNT(*) AS n FROM quant_capital
           WHERE obj_type='industry' GROUP BY run_date, style ORDER BY run_date"""
    )


def load_position_limit(db: str) -> dict:
    """当前评级与建议总仓位上限（复用纪律层映射逻辑）。"""
    from invest.discipline.rating import get_position_limit, get_rating
    conn = connect(db)
    try:
        macro = get_rating(conn, "macro")
        market = get_rating(conn, "market")
        return {
            "macro": (macro or {}).get("value"),
            "market": (market or {}).get("value"),
            "position_limit": float(get_position_limit(conn)),
        }
    finally:
        conn.close()


def _last_closed_trading_day(now: pd.Timestamp | None = None) -> pd.Timestamp:
    """最近一个**已收盘**的交易日（健康判定的参照日）。

    交易日 15:30 之后 → 当天；否则回退上一交易日（周末/节假日继续回退）。
    2026-09-15 修正：原先拿 today 比较并把"滞后≤1 天"判为正常，
    导致交易日内缺当日数据（15:00 后仍只有昨日）也显示「正常」。
    """
    from invest.data.calendar import is_trading_day, latest_trading_day

    ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now()
    day = ts.date()
    if not (is_trading_day(day) and ts.time() >= dt.time(15, 30)):
        day = latest_trading_day(day - dt.timedelta(days=1))
    return pd.Timestamp(day)


# 各表允许的"正常"滞后天数（相对参照日）。dragon_tiger 只在有上榜日才有数据，
# 故容 1 天；其余行情/情绪/估值表当日必须有，容 0 天。
_HEALTH_TOLERANCE = {"dragon_tiger": 1}
_MACRO_TOLERANCE_DAYS = 45  # 月度数据（社融/PMI 等）天然滞后约一个月，放宽到 45 天


def _health_status(lag_days: float, tolerance: int) -> str:
    if pd.isna(lag_days):
        return "过期"
    if lag_days <= tolerance:
        return "正常"
    if lag_days <= tolerance + 2:
        return "偏旧"
    return "过期"


def _parse_month(value) -> pd.Timestamp | None:
    """解析月度口径日期：2026-08 / 202608 / 2026年08月份 / 2026-08-01 → 该月月末。"""
    text = str(value or "").strip()
    if not text or text.lower() in {"nat", "none"}:
        return None
    m = re.match(r"^(\d{4})\D{0,3}(\d{1,2})", text)
    if m:
        year, month = int(m.group(1)), min(max(int(m.group(2)), 1), 12)
        return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    ts = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(ts) else pd.Timestamp(ts).normalize() + pd.offsets.MonthEnd(0)


def load_data_health(db: str) -> pd.DataFrame:
    """各行情表最新日期与滞后天数（数据健康横幅）。

    参照日 = 最近已收盘交易日（见 _last_closed_trading_day）；macro_series 按月解析、
    用 45 天容忍度单独判定（原先 MAX(date) 解析不出中文月份 → 一直显示 NaT/过期）。
    """
    sql = """
        SELECT 'industry_bars' AS tbl, MAX(date) AS max_date FROM industry_bars
        UNION ALL SELECT 'index_bars', MAX(date) FROM index_bars
        UNION ALL SELECT 'daily_bars', MAX(date) FROM daily_bars
        UNION ALL SELECT 'market_emotion', MAX(date) FROM market_emotion
        UNION ALL SELECT 'industry_valuation', MAX(date) FROM industry_valuation
        UNION ALL SELECT 'dragon_tiger', MAX(date) FROM dragon_tiger
    """
    df = _read(db, sql)
    if df.empty:
        return df
    ref = _last_closed_trading_day()
    df["max_date"] = pd.to_datetime(df["max_date"], format="mixed", errors="coerce")
    df["lag_days"] = (ref - df["max_date"]).dt.days
    df["status"] = [
        _health_status(lag, _HEALTH_TOLERANCE.get(tbl, 0))
        for tbl, lag in zip(df["tbl"], df["lag_days"])
    ]

    macro = _read(db, "SELECT MAX(date) AS max_date FROM macro_series")
    if not macro.empty and macro.iloc[0]["max_date"] is not None:
        month_end = _parse_month(macro.iloc[0]["max_date"])
        lag = float((ref - month_end).days) if month_end is not None else float("nan")
        macro_row = pd.DataFrame([{
            "tbl": "macro_series",
            "max_date": month_end,
            "lag_days": lag,
            "status": _health_status(
                lag,
                _MACRO_TOLERANCE_DAYS if month_end is not None else 0,
            ),
        }])
        df = pd.concat([df, macro_row], ignore_index=True)

    df["ref_date"] = ref
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


_BIGV_COLS = [
    "id", "name", "xueqiu_id", "homepage", "watched",
    "last_harvest_at", "last_harvest_new", "last_harvest_error",
    "backfill_done", "persona_updated_at", "n_opinions",
]


def load_bigv_profiles(db: str) -> pd.DataFrame:
    try:
        return _read(
            db,
            """SELECT p.id, p.name, p.xueqiu_id, p.homepage, p.watched,
                      p.last_harvest_at, p.last_harvest_new, p.last_harvest_error,
                      p.backfill_done, p.persona_updated_at,
                      (SELECT COUNT(*) FROM big_v_opinion o WHERE o.profile_id=p.id) AS n_opinions
               FROM big_v_profile p
               WHERE p.watched=1
               ORDER BY p.name COLLATE NOCASE""",
        )
    except Exception:
        return pd.DataFrame(columns=_BIGV_COLS)


def register_bigv(db: str, name: str, xueqiu: str) -> dict:
    from invest.bigv.watch import register

    try:
        conn = connect(db)
        try:
            return register(conn, name=name, xueqiu=xueqiu)
        finally:
            conn.close()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def ask_bigv(db: str, profile_id: str, question: str, mode: str = "grounded") -> dict:
    from invest.bigv.ask import ask_big_v

    try:
        conn = connect(db)
        try:
            return ask_big_v(conn, profile_id=profile_id, question=question, mode=mode)
        finally:
            conn.close()
    except Exception as exc:
        return {"error": str(exc), "answer": ""}


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


def load_actions(db: str, asof=None) -> pd.DataFrame:
    """动作清单只读。缺表返回空表。"""
    from invest.actions.query import list_actions

    cols = [
        "date", "symbol", "verb", "priority", "source", "entry_lo", "entry_hi",
        "stop_loss", "target", "status", "hint", "position_hint",
    ]
    conn = connect(db)
    try:
        rows = list_actions(conn, asof)
    finally:
        conn.close()
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


def load_watch(db: str, status: str = "open") -> pd.DataFrame:
    """观察名单只读。缺表返回空表。"""
    from invest.actions.watch import list_watch

    cols = ["symbol", "source", "reason", "status", "in_date", "expire_date"]
    conn = connect(db)
    try:
        rows = list_watch(conn, status=status)
    finally:
        conn.close()
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


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


_REPORT_JOBS = (
    "morning_brief", "auction", "evening_report", "premarket",
    "action_digest", "action_digest_pm",
)


def load_report_jobs(db: str, limit: int = 12) -> pd.DataFrame:
    """报告/推送类任务最近留痕（含 detail，供数据状态页回看）。"""
    marks = ",".join("?" * len(_REPORT_JOBS))
    try:
        return _read(
            db,
            f"""SELECT job, status, started_at, finished_at, detail
                FROM job_runs WHERE job IN ({marks})
                ORDER BY id DESC LIMIT ?""",
            (*_REPORT_JOBS, limit),
        )
    except Exception:
        return pd.DataFrame(columns=["job", "status", "started_at", "finished_at", "detail"])


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


def resolve_workbench_as_of(db: str, as_of: str | None = None) -> str:
    """工作台时点：显式 as_of 优先；空则取已落库事实卡最新日期；再没有才用当天。"""
    import datetime as dt

    raw = (as_of or "").strip()
    if raw:
        return raw
    conn = connect(db)
    try:
        row = conn.execute("SELECT MAX(as_of) AS as_of FROM fact_cards").fetchone()
        latest = row["as_of"] if row else None
    finally:
        conn.close()
    if latest:
        return str(latest)
    return dt.date.today().isoformat()


def load_fact_cards(
    db: str,
    as_of: str | None = None,
    obj_type: str | None = "industry",
    dimension: str | None = None,
) -> pd.DataFrame:
    """中期比价工作台：事实卡列表，可按 as_of / 维度筛选。"""
    sql = """SELECT id, obj_type, obj, as_of, data_version, rule_version,
                    dimensions_json, missing_json, created_at
             FROM fact_cards WHERE 1=1"""
    args: list = []
    if as_of:
        sql += " AND as_of=?"
        args.append(as_of)
    if obj_type:
        sql += " AND obj_type=?"
        args.append(obj_type)
    sql += " ORDER BY obj"
    df = _read(db, sql, tuple(args))
    if df.empty or not dimension:
        return df
    import json

    keep = []
    for idx, row in df.iterrows():
        missing = json.loads(row["missing_json"] or "[]")
        dims = json.loads(row["dimensions_json"] or "{}")
        if dimension in missing or not dims.get(dimension):
            continue
        keep.append(idx)
    return df.loc[keep].reset_index(drop=True)


def load_fact_card_detail(db: str, card_id: int) -> dict:
    conn = connect(db)
    try:
        row = conn.execute("SELECT * FROM fact_cards WHERE id=?", (card_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def load_fact_evidence(db: str, card_id: int) -> pd.DataFrame:
    return _read(
        db,
        """SELECT evidence_id, kind, source, url, published_at, as_of, summary
           FROM fact_evidence WHERE card_id=? ORDER BY id""",
        (card_id,),
    )


def load_comparisons(db: str, as_of: str | None = None) -> pd.DataFrame:
    sql = """SELECT id, as_of, peer_set_json, conclusion, notes,
                    data_version, rule_version, created_at
             FROM comparison_records"""
    args: list = []
    if as_of:
        sql += " WHERE as_of=?"
        args.append(as_of)
    sql += " ORDER BY id DESC"
    return _read(db, sql, tuple(args))


def save_comparison(
    db: str,
    *,
    as_of: str,
    peer_set: list[str],
    conclusion: str,
    notes: str = "",
) -> dict:
    from invest.evidence.factcards import record_comparison

    conn = connect(db)
    try:
        return record_comparison(
            conn, as_of=as_of, peer_set=peer_set, conclusion=conclusion, notes=notes,
        )
    finally:
        conn.close()


def run_deep_dive(
    db: str,
    industries: list[str],
    *,
    as_of: str,
    symbols: list[str] | None = None,
    news_fn=None,
) -> dict:
    """仪表盘深查：仅允许 3–5 个行业 + 候选池/指定个股 ≤20，并落库。

    2026-08-31：默认使用 deep_dive_news（电报快讯优先 + web 检索，URL 提取真实日期），
    深查由用户主动触发，token 可控。
    """
    from invest.evidence.factcards import deep_dive, deep_dive_news

    if news_fn is None:
        news_fn = deep_dive_news
    conn = connect(db)
    try:
        return deep_dive(
            conn, industries=industries, as_of=as_of, symbols=symbols, news_fn=news_fn,
        )
    finally:
        conn.close()


def find_evidence(db: str, evidence_id: str) -> dict:
    from invest.evidence.factcards import lookup_evidence

    conn = connect(db)
    try:
        found = lookup_evidence(conn, evidence_id)
        return found if found else {"error": f"未找到证据 {evidence_id}"}
    finally:
        conn.close()


def _workbench_symbol(symbol: str) -> str:
    """入池与建卡共用：600519.SH / sh600519 → 600519。"""
    from invest.data.quotes import normalize_symbol

    norm = normalize_symbol(symbol, "stock")
    if not norm:
        raise ValueError(f"非法或未规范化代码: {symbol!r}")
    return norm


def promote_factcard_to_pool(
    db: str,
    symbol: str,
    *,
    industry: str = "",
    reason: str = "事实卡人工比价",
    level: str = "track",
) -> dict:
    from invest.discipline.pool import add_to_pool

    symbol = _workbench_symbol(symbol)
    conn = connect(db)
    try:
        return add_to_pool(conn, symbol, level=level, industry=industry, reason=reason)
    finally:
        conn.close()


def promote_factcard_to_card(
    db: str,
    symbol: str,
    *,
    thesis: str,
    industry: str = "",
    reason: str = "事实卡人工比价",
) -> dict:
    from invest.discipline.cards import create_card

    symbol = _workbench_symbol(symbol)
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT symbol FROM candidate_pool WHERE symbol=? AND out_date IS NULL",
            (symbol,),
        ).fetchone()
        if row is None:
            promote_factcard_to_pool(db, symbol, industry=industry, reason=reason)
        return create_card(conn, symbol, thesis=thesis, cycle="mid")
    finally:
        conn.close()