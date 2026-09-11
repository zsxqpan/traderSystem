"""中线信号规则：只读 quant_* 表，不联网。horizon=mid，session=daily。"""
from __future__ import annotations

import logging
import sqlite3

from invest.signals.thresholds import (
    PATH_DAYS,
    QUAD_CHASE_LIMIT,
    QUAD_CROWDING,
    QUAD_HUNT_LIMIT,
    QUAD_RS_ZERO,
    QUAD_WATCH_LIMIT,
    ROTATION_LEAD_RANK,
    RS_INDUSTRY_TOP,
)
from invest.signals.types import Signal

logger = logging.getLogger(__name__)

_STAGE_IDS = {
    "启动": ("stage_start", "趋势启动"),
    "加速": ("stage_accel", "趋势加速"),
    "减速": ("stage_slow", "趋势减速"),
    "破位": ("stage_break", "趋势破位"),
}


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _classify(rs: float, crowding: float, state: str, stage: str) -> str | None:
    """互斥：avoid > chase > hunt > watch_cheap。极端且恶化即使 rs>0 也进 avoid。"""
    state = state or ""
    if state == "极端且恶化":
        return "avoid"
    crowded = crowding >= QUAD_CROWDING
    hot_state = "高拥挤" in state or "极端" in state
    if rs > QUAD_RS_ZERO and (crowded or hot_state):
        return "chase"
    if rs > QUAD_RS_ZERO and crowding < QUAD_CROWDING:
        return "hunt"
    if rs <= QUAD_RS_ZERO and crowded:
        return "avoid"
    if rs <= QUAD_RS_ZERO and crowding < QUAD_CROWDING and stage == "启动":
        return "watch_cheap"
    return None


def _latest_cross(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT s.obj, s.rs, s.trend_stage, v.crowding, v.crowding_state
           FROM quant_strength s
           JOIN quant_valuation v ON v.obj = s.obj
           WHERE s.period='short' AND s.obj_type='industry'
             AND s.run_date = (SELECT MAX(run_date) FROM quant_strength
                               WHERE period='short' AND obj_type='industry')
             AND v.run_date = (SELECT MAX(run_date) FROM quant_valuation)"""
    ).fetchall()
    out = []
    for r in rows:
        rs, crowding = _f(r["rs"]), _f(r["crowding"])
        if rs is None or crowding is None:
            continue
        out.append({
            "obj": r["obj"],
            "rs": rs,
            "trend_stage": r["trend_stage"] or "",
            "crowding": crowding,
            "crowding_state": r["crowding_state"] or "",
        })
    return out


def _quad_signal(qid: str, name: str, layer: str, row: dict) -> Signal:
    state = row["crowding_state"] or ""
    hint = (
        f"{row['obj']} rs={row['rs']:.2f} crowding={row['crowding']:.0%} "
        f"{state or '—'} {row['trend_stage']}"
    ).strip()
    return Signal(
        id=qid,
        name=name,
        session="daily",
        severity="watch",
        subject_type="sector",
        subject=row["obj"],
        hint=hint,
        evidence={
            "rs": round(row["rs"], 4),
            "crowding": round(row["crowding"], 4),
            "crowding_state": state,
            "trend_stage": row["trend_stage"],
        },
        horizon="mid",
        layer=layer,
    )


def quad_signals(conn: sqlite3.Connection) -> list[Signal]:
    """四象限：hunt/chase/watch_cheap/avoid。单行业互斥。"""
    buckets: dict[str, list[dict]] = {
        "hunt": [], "chase": [], "watch_cheap": [], "avoid": [],
    }
    for row in _latest_cross(conn):
        q = _classify(row["rs"], row["crowding"], row["crowding_state"], row["trend_stage"])
        if q:
            buckets[q].append(row)

    def _rs_desc(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda r: -r["rs"])

    hunt = sorted(
        buckets["hunt"],
        key=lambda r: (0 if r["crowding_state"] in ("正常", "升温") else 1, -r["rs"]),
    )[:QUAD_HUNT_LIMIT]
    chase = _rs_desc(buckets["chase"])[:QUAD_CHASE_LIMIT]
    cheap = _rs_desc(buckets["watch_cheap"])[:QUAD_WATCH_LIMIT]
    avoid = _rs_desc(buckets["avoid"])

    out: list[Signal] = []
    for row in hunt:
        out.append(_quad_signal("quad_hunt", "主战场", "discovery", row))
    for row in chase:
        out.append(_quad_signal("quad_chase", "追高风险", "discovery", row))
    for row in cheap:
        out.append(_quad_signal("quad_watch_cheap", "观察", "discovery", row))
    for row in avoid:
        out.append(_quad_signal("quad_avoid", "回避", "market", row))
    return out


def _strength_dates(conn: sqlite3.Connection, n: int) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT run_date FROM quant_strength
           WHERE period='short' AND obj_type='industry'
           ORDER BY REPLACE(run_date,'-','') DESC LIMIT ?""",
        (n,),
    ).fetchall()
    return sorted(r["run_date"] for r in rows)


def quad_path_signals(conn: sqlite3.Connection) -> list[Signal]:
    """近 PATH_DAYS：chase→hunt 拥挤回落仍强；hunt→chase 进入拥挤。无变化不出。"""
    dates = _strength_dates(conn, PATH_DAYS)
    if len(dates) < 2:
        return []
    ph = ",".join("?" * len(dates))
    rows = conn.execute(
        f"""SELECT s.run_date, s.obj, s.rs, s.trend_stage, v.crowding, v.crowding_state
            FROM quant_strength s
            JOIN quant_valuation v ON v.obj = s.obj AND v.run_date = s.run_date
            WHERE s.period='short' AND s.obj_type='industry'
              AND s.run_date IN ({ph})""",
        dates,
    ).fetchall()
    series: dict[str, dict[str, dict]] = {}
    for r in rows:
        rs, crowding = _f(r["rs"]), _f(r["crowding"])
        if rs is None or crowding is None:
            continue
        series.setdefault(r["obj"], {})[r["run_date"]] = {
            "rs": rs,
            "crowding": crowding,
            "crowding_state": r["crowding_state"] or "",
            "trend_stage": r["trend_stage"] or "",
        }
    first_d, last_d = dates[0], dates[-1]
    out: list[Signal] = []
    for obj, by_day in series.items():
        a, b = by_day.get(first_d), by_day.get(last_d)
        if not a or not b:
            continue
        qa = _classify(a["rs"], a["crowding"], a["crowding_state"], a["trend_stage"])
        qb = _classify(b["rs"], b["crowding"], b["crowding_state"], b["trend_stage"])
        if qa == "chase" and qb == "hunt":
            phrase = "拥挤回落仍强"
        elif qa == "hunt" and qb == "chase":
            phrase = "进入拥挤"
        else:
            continue
        hint = (
            f"{obj} {phrase} crowding={a['crowding']:.0%}→{b['crowding']:.0%} "
            f"rs={b['rs']:.2f} {b['crowding_state'] or '—'}"
        )
        out.append(Signal(
            id="quad_path",
            name="轨迹",
            session="daily",
            severity="info",
            subject_type="sector",
            subject=obj,
            hint=hint,
            evidence={
                "from": qa, "to": qb,
                "crowding_from": round(a["crowding"], 4),
                "crowding_to": round(b["crowding"], 4),
                "rs": round(b["rs"], 4),
            },
            horizon="mid",
            layer="discovery",
        ))
    return out


def stage_change_signals(conn: sqlite3.Connection) -> list[Signal]:
    """仅当日 trend_stage ≠ 昨日才出；映射启动/加速/减速/破位。"""
    dates = _strength_dates(conn, 2)
    if len(dates) < 2:
        return []
    prev_d, today_d = dates[0], dates[1]
    rows = conn.execute(
        """SELECT run_date, obj, trend_stage FROM quant_strength
           WHERE period='short' AND obj_type='industry' AND run_date IN (?, ?)""",
        (prev_d, today_d),
    ).fetchall()
    by_obj: dict[str, dict[str, str]] = {}
    for r in rows:
        by_obj.setdefault(r["obj"], {})[r["run_date"]] = r["trend_stage"] or ""
    out: list[Signal] = []
    for obj, stages in by_obj.items():
        prev, today = stages.get(prev_d, ""), stages.get(today_d, "")
        if not prev or not today or prev == today:
            continue
        mapped = _STAGE_IDS.get(today)
        if not mapped:
            continue
        sid, name = mapped
        hint = f"{obj} 趋势阶段 {prev}→{today}"
        out.append(Signal(
            id=sid,
            name=name,
            session="daily",
            severity="watch",
            subject_type="sector",
            subject=obj,
            hint=hint,
            evidence={"from": prev, "to": today},
            horizon="mid",
            layer="discovery",
        ))
    return out


def _mid_base(**kwargs) -> Signal:
    kwargs.setdefault("session", "daily")
    kwargs.setdefault("horizon", "mid")
    kwargs.setdefault("subject_type", "sector")
    kwargs.setdefault("severity", "watch")
    kwargs.setdefault("layer", "discovery")
    kwargs.setdefault("evidence", {})
    return Signal(**kwargs)


def rotation_signals(conn: sqlite3.Connection) -> list[Signal]:
    """最新日：领涨且 rank<=15 → lead；滞后 → lag。"""
    row = conn.execute("SELECT MAX(run_date) AS d FROM quant_rotation").fetchone()
    if not row or not row["d"]:
        return []
    out: list[Signal] = []
    for r in conn.execute(
        "SELECT industry, rank, lead_lag FROM quant_rotation WHERE run_date=?",
        (row["d"],),
    ):
        ind = r["industry"]
        lag = r["lead_lag"] or ""
        rank = r["rank"]
        if lag == "领涨" and rank is not None and int(rank) <= ROTATION_LEAD_RANK:
            out.append(_mid_base(
                id="rotation_lead", name="轮动领涨", subject=ind,
                hint=f"{ind} 领涨 rank={int(rank)}",
                evidence={"rank": int(rank), "lead_lag": lag},
            ))
        elif lag == "滞后":
            out.append(_mid_base(
                id="rotation_lag", name="轮动滞后", subject=ind,
                hint=f"{ind} 滞后 rank={rank}",
                evidence={"rank": rank, "lead_lag": lag},
            ))
    return out


def resonance_signals(conn: sqlite3.Connection) -> list[Signal]:
    """短线 RS TOP15 ∩ 主力净流入 TOP15，最多 5。"""
    strength = conn.execute(
        """SELECT obj, rs FROM quant_strength
           WHERE obj_type='industry' AND period='short'
             AND run_date=(SELECT MAX(run_date) FROM quant_strength
                           WHERE obj_type='industry' AND period='short')
           ORDER BY rs DESC LIMIT 15"""
    ).fetchall()
    flow = conn.execute(
        """SELECT industry, main_net FROM sector_fund_flow
           WHERE date=(SELECT MAX(date) FROM sector_fund_flow)
           ORDER BY main_net DESC LIMIT 15"""
    ).fetchall()
    net = {r["industry"]: r["main_net"] for r in flow}
    out: list[Signal] = []
    for r in strength:
        if r["obj"] not in net:
            continue
        rs = _f(r["rs"]) or 0.0
        mn = _f(net[r["obj"]]) or 0.0
        out.append(_mid_base(
            id="sector_resonance", name="板块共振", subject=r["obj"],
            hint=f"{r['obj']} rs={rs:+.1%} 主力净流入{mn / 1e8:+.2f}亿",
            evidence={"rs": round(rs, 4), "main_net": mn},
        ))
        if len(out) >= 5:
            break
    return out


def mid_rs_top_signals(conn: sqlite3.Connection) -> list[Signal]:
    """period=mid RS 前 8 且非破位。"""
    rows = conn.execute(
        """SELECT obj, rs, trend_stage FROM quant_strength
           WHERE obj_type='industry' AND period='mid'
             AND run_date=(SELECT MAX(run_date) FROM quant_strength
                           WHERE obj_type='industry' AND period='mid')
             AND (trend_stage IS NULL OR trend_stage != '破位')
           ORDER BY rs DESC LIMIT ?""",
        (RS_INDUSTRY_TOP,),
    ).fetchall()
    out: list[Signal] = []
    for r in rows:
        rs = _f(r["rs"])
        if rs is None:
            continue
        out.append(_mid_base(
            id="mid_rs_top", name="中线强度", subject=r["obj"],
            severity="info",
            hint=f"{r['obj']} 中线RS={rs:.2f} {r['trend_stage'] or ''}".strip(),
            evidence={"rs": round(rs, 4), "trend_stage": r["trend_stage"]},
        ))
    return out


def _mean_rs(by_code: dict[str, float], codes: list[str]) -> float | None:
    vals = [by_code[c] for c in codes if c in by_code]
    return sum(vals) / len(vals) if vals else None


def _sgn(x: float | None) -> int | None:
    if x is None:
        return None
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _index_rs_by_date(conn: sqlite3.Connection) -> list[tuple[str, dict[str, float]]]:
    rows = conn.execute(
        """SELECT DISTINCT run_date FROM quant_strength
           WHERE period='short' AND obj_type='index'
           ORDER BY REPLACE(run_date,'-','') DESC LIMIT 2"""
    ).fetchall()
    dates = sorted(r["run_date"] for r in rows)
    if len(dates) < 2:
        return []
    ph = ",".join("?" * len(dates))
    recs = conn.execute(
        f"""SELECT run_date, obj, rs FROM quant_strength
            WHERE period='short' AND obj_type='index' AND run_date IN ({ph})""",
        dates,
    ).fetchall()
    by_date: dict[str, dict[str, float]] = {d: {} for d in dates}
    for r in recs:
        rs = _f(r["rs"])
        if rs is None:
            continue
        by_date[r["run_date"]][r["obj"]] = rs
    return [(d, by_date[d]) for d in dates]


def style_shift_signals(conn: sqlite3.Connection) -> list[Signal]:
    """大小盘（1000+500 vs 50+300）或成长（科创+创业）RS 差符号翻转才出。"""
    series = _index_rs_by_date(conn)
    if len(series) < 2:
        return []
    (_, prev), (_, today) = series[0], series[1]
    size_s, size_l = ["000852", "000905"], ["000016", "000300"]
    growth_codes = ["000688", "399006"]
    small, large = _mean_rs(prev, size_s), _mean_rs(prev, size_l)
    size_prev = (small - large) if small is not None and large is not None else None
    small_t, large_t = _mean_rs(today, size_s), _mean_rs(today, size_l)
    size_today = (small_t - large_t) if small_t is not None and large_t is not None else None
    g_prev, g_today = _mean_rs(prev, growth_codes), _mean_rs(today, growth_codes)
    flips = []
    if _sgn(size_prev) not in (None, 0) and _sgn(size_today) not in (None, 0) and _sgn(size_prev) != _sgn(size_today):
        flips.append("大小盘")
    if _sgn(g_prev) not in (None, 0) and _sgn(g_today) not in (None, 0) and _sgn(g_prev) != _sgn(g_today):
        flips.append("成长")
    if not flips:
        return []
    return [_mid_base(
        id="style_shift", name="风格切换", subject="风格",
        subject_type="market", layer="market",
        hint=f"风格切换：{'、'.join(flips)} RS 差变号",
        evidence={"flips": flips, "size_from": size_prev, "size_to": size_today},
    )]


def emotion_stage_shift_signals(conn: sqlite3.Connection) -> list[Signal]:
    """最近两日 emotion_cycle stage 变化才出。"""
    try:
        import pandas as pd

        from invest.quant.emotion_cycle import cycle_series
    except Exception:
        return []
    df = pd.read_sql_query(
        "SELECT date, limit_up_count, max_lianban, zhaban_rate FROM market_emotion ORDER BY date",
        conn,
    )
    if df is None or len(df) < 2:
        return []
    ser = cycle_series(df)
    if ser is None or len(ser) < 2:
        return []
    a, b = ser.iloc[-2], ser.iloc[-1]
    from_s, to_s = str(a["stage"]), str(b["stage"])
    if from_s == to_s or from_s == "数据不足" or to_s == "数据不足":
        return []
    return [_mid_base(
        id="emotion_stage_shift", name="情绪切换", subject="情绪",
        subject_type="market", layer="market",
        hint=f"情绪周期 {from_s}→{to_s}",
        evidence={"from": from_s, "to": to_s},
    )]


def mid_signals(conn: sqlite3.Connection) -> list[Signal]:
    """全部中线规则（scan session=daily 调用）。单规则失败不影响其余。"""
    out: list[Signal] = []
    for fn in (
        quad_signals,
        quad_path_signals,
        stage_change_signals,
        rotation_signals,
        resonance_signals,
        mid_rs_top_signals,
        style_shift_signals,
        emotion_stage_shift_signals,
    ):
        try:
            out.extend(fn(conn))
        except Exception as exc:
            logger.warning("中线规则 %s 失败: %s", fn.__name__, exc)
    return out
