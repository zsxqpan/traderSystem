"""规则优先合成动作清单：每标的一行。"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date

from invest.actions.types import Action

logger = logging.getLogger(__name__)

SIGNAL_CAP = 8
PICK_CAP = 3
_SYM_RE = re.compile(r"^\d{6}$")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_SLOGAN_RE = re.compile(r"建议买入")
_NEG_RE = re.compile(r"暂?不(建议|要|必|再)?(买入|买|加仓|加|卖出|卖|清仓|清)")

_LLM_VERB = (
    ("减", "reduce"),
    ("止盈", "reduce"),
    ("加", "add"),
    ("清", "sell"),
    ("卖", "sell"),
    ("止损", "sell"),
    ("买", "buy"),
    ("介入", "buy"),
    ("等", "wait"),
    ("观望", "wait"),
    ("看", "watch"),
    ("观察", "watch"),
    ("持", "hold"),
)


def _iso(d: date | str) -> str:
    if hasattr(d, "isoformat"):
        return d.isoformat()[:10]
    return str(d)[:10]


def _f(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_range(text: str | None) -> tuple[float | None, float | None]:
    if not text:
        return None, None
    nums = [float(x) for x in _NUM_RE.findall(str(text))]
    if len(nums) >= 2:
        return min(nums[0], nums[1]), max(nums[0], nums[1])
    if len(nums) == 1:
        return nums[0], nums[0]
    return None, None


def valid_symbol(raw) -> str | None:
    s = str(raw or "").strip()
    return s if _SYM_RE.match(s) else None


def _clean_text(s: str) -> str:
    return " ".join(_SLOGAN_RE.sub("", str(s or "")).split()).strip("，,；; ")


def llm_verb(action_text: str | None) -> str | None:
    t = _SLOGAN_RE.sub("", str(action_text or ""))
    t = _NEG_RE.sub("", t)
    for key, verb in _LLM_VERB:
        if key in t:
            return verb
    return None


def _closes(conn: sqlite3.Connection, symbols: list[str], asof: str) -> dict[str, float]:
    if not symbols:
        return {}
    q = ",".join("?" * len(symbols))
    asof_n = str(asof).replace("-", "")[:8]
    rows = conn.execute(
        f"""SELECT d.symbol, d.close FROM daily_bars d
            JOIN (SELECT symbol, MAX(REPLACE(date,'-','')) md FROM daily_bars
                  WHERE symbol IN ({q}) AND REPLACE(date,'-','')<=?
                  GROUP BY symbol) m
              ON d.symbol=m.symbol AND REPLACE(d.date,'-','')=m.md""",
        [*symbols, asof_n],
    ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        c = _f(r["close"])
        if c is not None and c > 0:
            out[r["symbol"]] = c
    return out


def _position_hint(level: str | None, entry_lo, entry_hi, stop) -> str:
    if not level or stop is None:
        return ""
    mid = None
    if entry_lo is not None and entry_hi is not None:
        mid = (float(entry_lo) + float(entry_hi)) / 2.0
    if mid is None:
        return ""
    from invest.discipline.position import LEVEL_CAP, LEVEL_RISK, SINGLE_STOCK_CAP

    r = LEVEL_RISK.get(level.upper(), LEVEL_RISK["B"])
    cap = min(LEVEL_CAP.get(level.upper(), 0.10), SINGLE_STOCK_CAP)
    return f"固定风险 R={r:.2%}，个股帽 {cap:.0%}"


def _hint(symbol: str, verb: str, *, close=None, lo=None, hi=None, stop=None) -> str:
    parts = [symbol]
    if stop is not None and close is not None:
        if close <= stop:
            parts.append(f"收盘 {close:g} ≤ 止损 {stop:g}")
        else:
            parts.append(f"止损 {stop:g}，收盘 {close:g} 未破")
    elif stop is not None:
        parts.append(f"止损 {stop:g}")
    if lo is not None and hi is not None:
        if close is not None and lo <= close <= hi:
            parts.append(f"收盘 {close:g} 在区间 {lo:g}-{hi:g}")
        else:
            parts.append(f"区间 {lo:g}-{hi:g}")
    if verb == "watch" and len(parts) == 1:
        parts.append("观察")
    return "，".join(parts)


def compose(
    conn: sqlite3.Connection,
    asof: date | str,
    *,
    for_date: date | str | None = None,
    llm_plan: dict | None = None,
    closes: dict[str, float] | None = None,
) -> list[Action]:
    """合成 for_date（默认 asof）的动作。不写 candidate_pool，不联网。"""
    day = _iso(for_date or asof)
    sig_day = _iso(asof)
    cards = {
        r["symbol"]: dict(r)
        for r in conn.execute(
            "SELECT * FROM cards WHERE status IN ('locked','review')"
        )
    }
    plans = {
        r["symbol"]: dict(r)
        for r in conn.execute("SELECT * FROM trade_plans WHERE status='active'")
    }
    pool = {
        r["symbol"]: dict(r)
        for r in conn.execute(
            "SELECT * FROM candidate_pool WHERE level IN ('core','track') "
            "AND out_date IS NULL"
        )
    }
    symbols = sorted(set(cards) | set(plans) | set(pool))
    px = dict(closes or {})
    missing = [s for s in symbols if s not in px]
    if missing:
        px.update(_closes(conn, missing, sig_day))

    out: dict[str, Action] = {}
    for sym in symbols:
        card, plan, po = cards.get(sym), plans.get(sym), pool.get(sym)
        lo = hi = stop = tgt = None
        source = "pool"
        source_ref = ""
        invalid = ""
        level = None
        if card:
            lo, hi = parse_range(card.get("entry_range"))
            stop = _f(card.get("stop_loss"))
            tgt = _f(card.get("target"))
            source = "card"
            source_ref = str(card.get("id") or "")
            invalid = card.get("falsify") or ""
            level = card.get("level")
        if plan:
            plo, phi = parse_range(plan.get("buy_range"))
            if plo is not None:
                lo, hi = plo, phi
            if _f(plan.get("stop_loss")) is not None:
                stop = _f(plan.get("stop_loss"))
            if plan.get("take_profit"):
                tlo, thi = parse_range(plan.get("take_profit"))
                tgt = thi or tlo or tgt
            source = "plan"
            source_ref = str(plan.get("id") or source_ref)
            if plan.get("invalid_condition"):
                invalid = plan["invalid_condition"]
        if po and po.get("falsify_condition") and not invalid:
            invalid = po["falsify_condition"]

        close = px.get(sym)
        stop_broken = (
            stop is not None and stop > 0
            and close is not None and close > 0
            and close <= stop
        )
        has_card = sym in cards
        in_range = (
            lo is not None and hi is not None and close is not None
            and lo <= close <= hi
        )
        if stop_broken:
            verb, priority = "sell", 1
        elif has_card:
            verb = "add" if in_range else ("wait" if lo is not None else "hold")
            priority = 2
        elif in_range:
            verb, priority = "buy", 2
        elif lo is not None:
            verb, priority = "wait", 2
        else:
            verb, priority = "hold", 2
        out[sym] = Action(
            date=day, symbol=sym, verb=verb, priority=priority, source=source,
            source_ref=source_ref, entry_lo=lo, entry_hi=hi, stop_loss=stop,
            target=tgt, invalid_condition=invalid,
            position_hint=_position_hint(level, lo, hi, stop) if source == "card" else "",
            hint=_hint(sym, verb, close=close, lo=lo, hi=hi, stop=stop),
            evidence={"close": close} if close is not None else {},
        )

    # 短线 action 个股信号
    try:
        sig_rows = conn.execute(
            """SELECT signal_id, subject, hint FROM trade_signals
               WHERE date=? AND horizon='short' AND severity='action'
                 AND subject_type='stock'
               ORDER BY CASE session WHEN 'close' THEN 0
                         WHEN 'intraday' THEN 1 ELSE 2 END,
                        signal_id, subject""",
            (sig_day,),
        ).fetchall()
    except Exception:
        sig_rows = []
    added_sig = 0
    for r in sig_rows:
        sub = valid_symbol(r["subject"])
        if not sub:
            continue
        if sub in out:
            ev = out[sub].evidence
            ev.setdefault("signals", []).append(r["signal_id"])
            continue
        if added_sig >= SIGNAL_CAP:
            continue
        out[sub] = Action(
            date=day, symbol=sub, verb="watch", priority=3, source="signal",
            source_ref=r["signal_id"],
            hint=_clean_text(r["hint"] or f"{sub} {r['signal_id']}"),
            evidence={"signals": [r["signal_id"]]},
        )
        added_sig += 1

    plan = llm_plan or {}
    for item in plan.get("plans") or []:
        sub = valid_symbol(item.get("symbol"))
        if not sub or sub not in out:
            continue
        a = out[sub]
        if a.priority == 1:
            continue
        mapped = llm_verb(item.get("action"))
        if mapped:
            a.verb = mapped
        extra = _clean_text(item.get("action") or "")
        if extra and extra not in a.hint:
            a.hint = f"{a.hint}；{extra}" if a.hint else extra

    added_pick = 0
    for item in plan.get("picks") or []:
        if added_pick >= PICK_CAP:
            break
        sub = valid_symbol(item.get("symbol"))
        if not sub or sub in out:
            continue
        reason = _clean_text(item.get("reason") or item.get("plan") or "探索") or "探索"
        out[sub] = Action(
            date=day, symbol=sub, name=item.get("name") or "",
            verb="watch", priority=4, source="llm",
            hint=_clean_text(f"{sub} {reason}"),
            evidence={"pick": True},
        )
        added_pick += 1

    return sorted(out.values(), key=lambda a: (a.priority, a.symbol))
