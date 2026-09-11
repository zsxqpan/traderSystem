"""短线交易信号规则（纯函数，注入行情与日线统计）。"""
from __future__ import annotations

import datetime as dt
import statistics

from invest.signals.bars import is_20cm
from invest.signals.thresholds import (
    AUCTION_YOY,
    BOARD_HIGH_OPEN_PCT,
    BOARD_LOW_OPEN_PCT,
    BOARD_TOP_N,
    BREADTH_SHIFT,
    COLLECTIVE_MIN,
    COLLECTIVE_SHARE,
    COLLECTIVE_VR,
    FLOW_SPIKE,
    HIGH_NEAR,
    HIGH_VOL_RATIO,
    KEEP_AMOUNT,
    KEEP_VOL_WATCH,
    KEEP_VOL_ZT,
    LADDER_OPEN_STRONG,
    LADDER_OPEN_WEAK,
    LIANBAN_HIGH,
    OUTLIER_PCT,
    OUTLIER_PCT_20CM,
    RET5_HIGH,
    RS_LEADER_RANK,
    RS_LEADER_UP,
    SHRINK_DIVERGE,
    SHRINK_EXTREME,
    ZHABAN_HOT,
)
from invest.signals.timeutil import skip_early_volume, trading_elapsed_fraction
from invest.signals.types import Signal


def _pct(q: dict | None) -> float | None:
    """报价涨跌幅统一为百分点（1.2 = +1.2%）。"""
    if not q:
        return None
    v = q.get("pct")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _vol(q: dict | None) -> float:
    if not q:
        return 0.0
    try:
        return float(q.get("vol") or 0)
    except (TypeError, ValueError):
        return 0.0


def _price(q: dict | None) -> float | None:
    if not q or q.get("price") is None:
        return None
    try:
        return float(q["price"])
    except (TypeError, ValueError):
        return None


def _at_high(stats: dict | None, price: float | None, lianban: int) -> bool:
    if not stats:
        return lianban >= LIANBAN_HIGH
    if lianban >= LIANBAN_HIGH:
        return True
    ret5 = stats.get("ret5")
    if ret5 is not None and ret5 >= RET5_HIGH:
        return True
    high20 = stats.get("high20") or 0.0
    px = price if price is not None else stats.get("today_close") or stats.get("prev_close")
    return bool(high20 > 0 and px is not None and px >= high20 * (1 - HIGH_NEAR))


def _vr(today_vol: float, avg5: float, elapsed: float) -> float | None:
    if avg5 <= 0 or today_vol < 0:
        return None
    if elapsed <= 0:
        return None
    return today_vol / (avg5 * elapsed)


def auction_volume_signals(
    session: str,
    watch: list[str],
    zt: list[dict],
    quotes: dict[str, dict],
    stats: dict[str, dict],
    lianban: dict[str, int],
) -> list[Signal]:
    out: list[Signal] = []
    zt_set = {r["symbol"] for r in zt}
    names = {r["symbol"]: r.get("name") or r["symbol"] for r in zt}
    universe = list(dict.fromkeys(list(watch) + list(zt_set)))
    for sym in universe:
        q = quotes.get(sym)
        st = stats.get(sym)
        yvol = (st or {}).get("yday_vol") or 0.0
        avol = _vol(q)
        if yvol <= 0 or avol <= 0:
            continue
        ratio = avol / yvol
        price = _price(q)
        amount = None
        if price is not None:
            amount = price * avol * 100.0
        is_zt = sym in zt_set or lianban.get(sym, 0) >= 1
        thr = KEEP_VOL_ZT if is_zt else KEEP_VOL_WATCH
        keep = ratio >= thr or (amount is not None and amount >= KEEP_AMOUNT)
        pct = _pct(q) or 0.0
        if keep:
            sev = "action" if is_zt and ratio >= KEEP_VOL_ZT else "watch"
            out.append(Signal(
                id="auction_keep_vol", name="竞价保量", session=session, severity=sev,
                subject_type="stock", subject=sym,
                hint=f"竞价量/昨量 {ratio:.1%}（{'连板/昨涨停' if is_zt else '关注票'}）",
                evidence={"ratio": round(ratio, 4), "auction_vol": avol, "yday_vol": yvol},
            ))
        elif is_zt and pct > 0 and ratio < SHRINK_DIVERGE:
            out.append(Signal(
                id="auction_shrink_diverge", name="竞价缩量分歧", session=session,
                severity="watch", subject_type="stock", subject=sym,
                hint=f"{names.get(sym, sym)} 高开{pct:+.2f}% 但竞价量/昨量仅{ratio:.1%}，虚高无量",
                evidence={"ratio": round(ratio, 4), "pct": pct},
            ))
    return out


def auction_keep_vol_yoy_signals(
    session: str,
    watch: list[str],
    zt: list[dict],
    quotes: dict[str, dict],
    yoy_vols: dict[str, float],
) -> list[Signal]:
    """今竞价量 / 昨竞价快照量 ≥ AUCTION_YOY。无昨快照则跳过该票。"""
    out: list[Signal] = []
    zt_set = {r["symbol"] for r in zt}
    universe = list(dict.fromkeys(list(watch) + list(zt_set)))
    for sym in universe:
        try:
            yv = float(yoy_vols.get(sym) or 0)
        except (TypeError, ValueError):
            yv = 0.0
        avol = _vol(quotes.get(sym))
        if yv <= 0 or avol <= 0:
            continue
        ratio = avol / yv
        if ratio >= AUCTION_YOY:
            out.append(Signal(
                id="auction_keep_vol_yoy", name="竞价同比保量", session=session,
                severity="watch", subject_type="stock", subject=sym,
                hint=f"今竞价量/昨竞价量 {ratio:.1%}",
                evidence={"ratio": round(ratio, 4), "auction_vol": avol, "yday_auction_vol": yv},
            ))
    return out


def board_open_vol_signals(session: str, boards: list[dict] | None) -> list[Signal]:
    """高开/低开放量。boards=None 不出信号、不联网。"""
    if not boards:
        return []
    vol_ranked = sorted(
        [b for b in boards if isinstance(b, dict) and b.get("symbol")],
        key=lambda b: float(b.get("vol") or 0),
        reverse=True,
    )
    vol_set = {
        b["symbol"] for b in vol_ranked[:BOARD_TOP_N] if float(b.get("vol") or 0) > 0
    }
    out: list[Signal] = []
    seen: set[str] = set()
    for b in boards:
        if not isinstance(b, dict):
            continue
        sym = b.get("symbol")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        try:
            pct = float(b.get("pct") or 0)
        except (TypeError, ValueError):
            continue
        try:
            amount = float(b["amount"]) if b.get("amount") is not None else 0.0
        except (TypeError, ValueError):
            amount = 0.0
        if amount < KEEP_AMOUNT and sym not in vol_set:
            continue
        name = b.get("name") or sym
        if pct >= BOARD_HIGH_OPEN_PCT:
            out.append(Signal(
                id="board_high_open_vol", name="高开放量", session=session,
                severity="watch", subject_type="stock", subject=sym, layer="discovery",
                hint=f"{name} 高开{pct:+.2f}% 放量",
                evidence={"pct": pct, "amount": amount, "vol": b.get("vol")},
            ))
        elif pct <= BOARD_LOW_OPEN_PCT:
            out.append(Signal(
                id="board_low_open_vol", name="低开放量", session=session,
                severity="info", subject_type="stock", subject=sym, layer="discovery",
                hint=f"{name} 低开{pct:+.2f}% 放量",
                evidence={"pct": pct, "amount": amount, "vol": b.get("vol")},
            ))
    return out


def auction_height_signals(
    session: str,
    zt: list[dict],
    quotes: dict[str, dict],
) -> list[Signal]:
    if len(zt) < 2:
        return []
    pcts = []
    for r in zt:
        p = _pct(quotes.get(r["symbol"]))
        if p is not None:
            pcts.append(p)
    if len(pcts) < 2:
        return []
    up = sum(1 for p in pcts if p > 0)
    rate = up / len(pcts)
    out: list[Signal] = []
    if rate >= LADDER_OPEN_STRONG:
        out.append(Signal(
            id="auction_height", name="连板高开率", session=session, severity="info",
            subject_type="market", subject="连板",
            hint=f"连板高开{up}/{len(pcts)}={rate:.0%}，承接偏强",
            evidence={"open_rate": round(rate, 4), "n": len(pcts)},
        ))
    elif rate <= LADDER_OPEN_WEAK:
        out.append(Signal(
            id="auction_height", name="连板高开率", session=session, severity="watch",
            subject_type="market", subject="连板",
            hint=f"连板高开{up}/{len(pcts)}={rate:.0%}，分歧/退潮预警",
            evidence={"open_rate": round(rate, 4), "n": len(pcts)},
        ))
    max_lb = max(int(r.get("lianban") or 0) for r in zt)
    top = [r for r in zt if int(r.get("lianban") or 0) == max_lb]
    top_pcts = [_pct(quotes.get(r["symbol"])) for r in top]
    top_pcts = [p for p in top_pcts if p is not None]
    if top_pcts and max_lb >= 3:
        avg = sum(top_pcts) / len(top_pcts)
        if avg < 0:
            out.append(Signal(
                id="auction_height", name="最高板折价", session=session, severity="watch",
                subject_type="market", subject="最高板",
                hint=f"昨{max_lb}板今日均折价{avg:+.2f}%",
                evidence={"max_lianban": max_lb, "avg_pct": round(avg, 3)},
            ))
    return out


def shrink_highvol_signals(
    session: str,
    now: dt.datetime,
    watch: list[str],
    quotes: dict[str, dict],
    stats: dict[str, dict],
    lianban: dict[str, int],
) -> list[Signal]:
    out: list[Signal] = []
    early = session == "intraday" and skip_early_volume(now)
    elapsed = 1.0 if session == "close" else trading_elapsed_fraction(now)
    if session == "intraday" and (early or elapsed <= 0):
        return out
    for sym in watch:
        st = stats.get(sym)
        if not st:
            continue
        avg5 = st.get("avg5") or 0.0
        q = quotes.get(sym) or {}
        if session == "close":
            today_vol = st.get("today_vol")
            price = st.get("today_close")
            prev = st.get("prev_close") or 0.0
            pct = ((price / prev - 1) * 100.0) if price and prev else None
        else:
            today_vol = _vol(q) or None
            price = _price(q)
            pct = _pct(q)
            prev = st.get("prev_close") or 0.0
        if not today_vol or avg5 <= 0:
            continue
        vr = _vr(float(today_vol), avg5, elapsed)
        if vr is None:
            continue
        lb = lianban.get(sym, 0)
        at_high = _at_high(st, price, lb)
        if vr <= SHRINK_EXTREME:
            broken = price is not None and prev > 0 and price < prev
            hint = ("跌破昨收且极致缩量，无承接" if broken
                    else "不破昨收/均价，缩量洗盘观察")
            out.append(Signal(
                id="shrink_extreme", name="极致缩量", session=session,
                severity="action" if broken else "watch",
                subject_type="stock", subject=sym,
                hint=f"时间修正量比{vr:.2f}，{hint}",
                evidence={"vol_ratio": round(vr, 3), "broken": broken},
            ))
        if vr >= HIGH_VOL_RATIO and at_high:
            stall = pct is None or pct <= 1.0
            hint = "高位放量滞涨/冲高回落" if stall else "高位放量上涨"
            out.append(Signal(
                id="high_vol", name="高位放量", session=session,
                severity="action" if stall else "watch",
                subject_type="stock", subject=sym,
                hint=f"量比{vr:.1f}，{hint}",
                evidence={"vol_ratio": round(vr, 3), "pct": pct},
            ))
    return out


def sector_signals(
    session: str,
    now: dt.datetime,
    hot: list[dict],
    quotes: dict[str, dict],
    stats: dict[str, dict],
    lianban: dict[str, int],
) -> list[Signal]:
    out: list[Signal] = []
    elapsed = 1.0 if session == "close" else trading_elapsed_fraction(now)
    if session == "intraday" and (skip_early_volume(now) or elapsed <= 0):
        elapsed_ok = False
    else:
        elapsed_ok = elapsed > 0
    for block in hot:
        stocks = block.get("stocks") or []
        name = block.get("block") or "板块"
        rows = []
        for s in stocks:
            sym = s["symbol"]
            st = stats.get(sym)
            q = quotes.get(sym) or {}
            pct = _pct(q)
            if session == "close" and pct is None and st:
                prev = st.get("prev_close") or 0.0
                tc = st.get("today_close")
                pct = ((tc / prev - 1) * 100.0) if tc and prev else None
            today_vol = _vol(q) if session != "close" else (st or {}).get("today_vol")
            avg5 = (st or {}).get("avg5") or 0.0
            vr = _vr(float(today_vol or 0), avg5, elapsed) if elapsed_ok and today_vol else None
            price = _price(q) if session != "close" else (st or {}).get("today_close")
            at_high = _at_high(st, price, lianban.get(sym, int(s.get("lianban") or 0)))
            rows.append({"symbol": sym, "pct": pct, "vr": vr, "at_high": at_high})
        n = len(rows)
        if n >= COLLECTIVE_MIN and elapsed_ok:
            hit = [r for r in rows if r["at_high"] and r["vr"] is not None and r["vr"] >= COLLECTIVE_VR]
            if len(hit) / n >= COLLECTIVE_SHARE:
                ev = "、".join(r["symbol"] for r in hit)
                out.append(Signal(
                    id="sector_collective", name="板块核心集体高位放量",
                    session=session, severity="action", subject_type="sector",
                    subject=name,
                    hint=f"{name} {len(hit)}/{n} 只核心高位放量（{ev}）",
                    evidence={"n": n, "hit": [r["symbol"] for r in hit]},
                ))
        pcts = [r["pct"] for r in rows if r["pct"] is not None]
        if n >= 2 and pcts:
            med = statistics.median(pcts)
            for r in rows:
                if r["pct"] is None:
                    continue
                thr = OUTLIER_PCT_20CM if is_20cm(r["symbol"]) else OUTLIER_PCT
                if abs(r["pct"] - med) >= thr:
                    out.append(Signal(
                        id="sector_outlier", name="板块内异动偏离",
                        session=session, severity="watch", subject_type="stock",
                        subject=r["symbol"],
                        hint=f"{name}内 {r['symbol']} {r['pct']:+.2f}% vs 中位{med:+.2f}%",
                        evidence={"pct": r["pct"], "median": med, "block": name},
                    ))
    return out


def space_signals(
    conn,
    session: str,
    asof: dt.date,
) -> list[Signal]:
    from invest.signals.bars import compact

    out: list[Signal] = []
    cut = compact(asof)
    try:
        today_d = conn.execute(
            "SELECT MAX(date) AS d FROM limit_up_pool WHERE REPLACE(date,'-','') <= ?",
            (cut,),
        ).fetchone()["d"]
        yday_d = None
        if today_d:
            yday_d = conn.execute(
                "SELECT MAX(date) AS d FROM limit_up_pool WHERE REPLACE(date,'-','') < ?",
                (compact(today_d),),
            ).fetchone()["d"]
        today_rows = conn.execute(
            "SELECT symbol, lianban, zhaban FROM limit_up_pool WHERE date=?", (today_d,),
        ).fetchall() if today_d else []
        yday_rows = conn.execute(
            "SELECT symbol, lianban, zhaban FROM limit_up_pool WHERE date=?", (yday_d,),
        ).fetchall() if yday_d else []
    except Exception:
        today_rows, yday_rows = [], []

    def _zt(rows):
        return [r for r in rows if not r["zhaban"]]

    tzt, yzt = _zt(today_rows), _zt(yday_rows)
    tmax = max((int(r["lianban"] or 0) for r in tzt), default=0)
    ymax = max((int(r["lianban"] or 0) for r in yzt), default=0)
    if ymax and tmax and tmax < ymax:
        out.append(Signal(
            id="space_height", name="空间高度回落", session=session, severity="watch",
            subject_type="market", subject="高度",
            hint=f"最高板 {ymax}→{tmax}，高度回落",
            evidence={"yday_max": ymax, "today_max": tmax},
        ))
    if ymax >= 2:
        n = ymax
        y_nm1 = sum(1 for r in yzt if int(r["lianban"] or 0) == n - 1)
        t_n = sum(1 for r in tzt if int(r["lianban"] or 0) == n)
        if y_nm1 >= 1 and t_n / y_nm1 < 0.5:
            out.append(Signal(
                id="space_height", name="空间晋级失败", session=session, severity="watch",
                subject_type="market", subject="晋级",
                hint=f"昨{n-1}板{y_nm1}只，今{n}板{t_n}只，晋级失败",
                evidence={"y_nm1": y_nm1, "t_n": t_n, "n": n},
            ))

    try:
        emo = conn.execute(
            """SELECT date, limit_up_count, zhaban_rate FROM market_emotion
               WHERE REPLACE(date,'-','') <= ? ORDER BY REPLACE(date,'-','') DESC LIMIT 4""",
            (cut,),
        ).fetchall()
    except Exception:
        emo = []
    if len(emo) >= 2:
        latest = emo[0]
        prev = emo[1:]
        lu = latest["limit_up_count"]
        prev_lu = [r["limit_up_count"] for r in prev if r["limit_up_count"] is not None]
        if lu is not None and prev_lu:
            ma = sum(prev_lu) / len(prev_lu)
            if ma > 0:
                chg = (lu - ma) / ma
                if chg <= -BREADTH_SHIFT:
                    out.append(Signal(
                        id="space_breadth", name="空间广度收缩", session=session,
                        severity="watch", subject_type="market", subject="广度",
                        hint=f"涨停{lu}家 vs 3日均{ma:.0f}，回落{chg:.0%}",
                        evidence={"limit_up": lu, "ma3": round(ma, 1)},
                    ))
                elif chg >= BREADTH_SHIFT:
                    out.append(Signal(
                        id="space_breadth", name="空间广度扩张", session=session,
                        severity="info", subject_type="market", subject="广度",
                        hint=f"涨停{lu}家 vs 3日均{ma:.0f}，扩张{chg:.0%}",
                        evidence={"limit_up": lu, "ma3": round(ma, 1)},
                    ))
        zr = latest["zhaban_rate"]
        if zr is not None and zr >= ZHABAN_HOT:
            out.append(Signal(
                id="space_breadth", name="炸板升温", session=session, severity="watch",
                subject_type="market", subject="炸板",
                hint=f"炸板率{zr:.0%}，广度质量转差",
                evidence={"zhaban_rate": zr},
            ))
    return out


def etf_signals(session: str, etf_quotes: dict[str, dict] | None) -> list[Signal]:
    out: list[Signal] = []
    quotes = etf_quotes
    if quotes is None:
        try:
            from invest.data.etf import INDEX_ETFS, fetch_etf_quotes

            quotes = fetch_etf_quotes(list(INDEX_ETFS))
        except Exception:
            return out
    try:
        from invest.data.etf import big_money_signal, etf_line
    except Exception:
        return out
    for code, q in (quotes or {}).items():
        try:
            sig = big_money_signal(q)
        except Exception:
            sig = None
        if not sig:
            continue
        try:
            line = etf_line(q)
        except Exception:
            line = q.get("name") or code
        out.append(Signal(
            id="etf_big_money", name="ETF大资金", session=session, severity="watch",
            subject_type="etf", subject=code,
            hint=f"{line}（{sig}）",
            evidence={"code": code},
        ))
    return out


def sector_flow_spike_signals(conn) -> list[Signal]:
    """当日主力净流入 >0 且 ≥ 近 5 日均值 × FLOW_SPIKE。只挂 daily。"""
    try:
        dates = [
            r["date"]
            for r in conn.execute(
                "SELECT DISTINCT date FROM sector_fund_flow ORDER BY date DESC"
            )
        ]
    except Exception:
        return []
    if not dates:
        return []
    today, prev = dates[0], dates[1:6]
    if not prev:
        return []
    out: list[Signal] = []
    try:
        today_rows = conn.execute(
            "SELECT industry, main_net FROM sector_fund_flow WHERE date=?", (today,),
        ).fetchall()
    except Exception:
        return []
    for r in today_rows:
        try:
            net = float(r["main_net"]) if r["main_net"] is not None else 0.0
        except (TypeError, ValueError):
            continue
        if net <= 0:
            continue
        prev_vals: list[float] = []
        for d in prev:
            try:
                row = conn.execute(
                    "SELECT main_net FROM sector_fund_flow WHERE date=? AND industry=?",
                    (d, r["industry"]),
                ).fetchone()
            except Exception:
                row = None
            if row and row["main_net"] is not None:
                try:
                    prev_vals.append(float(row["main_net"]))
                except (TypeError, ValueError):
                    pass
        if not prev_vals:
            continue
        mean = sum(prev_vals) / len(prev_vals)
        if mean <= 0:
            continue
        if net >= mean * FLOW_SPIKE:
            out.append(Signal(
                id="sector_flow_spike", name="板块资金放大", session="daily",
                severity="watch", subject_type="sector", subject=r["industry"],
                horizon="short", layer="discovery",
                hint=f"主力净流入{net / 1e8:.1f}亿 ≥ 近{len(prev_vals)}日均{mean / 1e8:.1f}亿×{FLOW_SPIKE:.0f}",
                evidence={"main_net": net, "mean": round(mean, 2)},
            ))
    return out


def lianban_ladder_signals(conn, session: str, asof: dt.date) -> list[Signal]:
    """昨 n-1 今 n 未炸 → promote action；昨涨停今炸/未涨停 → fail watch。"""
    from invest.signals.bars import compact

    cut = compact(asof)
    try:
        today_d = conn.execute(
            "SELECT MAX(date) AS d FROM limit_up_pool WHERE REPLACE(date,'-','') <= ?",
            (cut,),
        ).fetchone()["d"]
        yday_d = None
        if today_d:
            yday_d = conn.execute(
                "SELECT MAX(date) AS d FROM limit_up_pool WHERE REPLACE(date,'-','') < ?",
                (compact(today_d),),
            ).fetchone()["d"]
        today_rows = conn.execute(
            "SELECT symbol, name, lianban, zhaban FROM limit_up_pool WHERE date=?",
            (today_d,),
        ).fetchall() if today_d else []
        yday_rows = conn.execute(
            "SELECT symbol, name, lianban, zhaban FROM limit_up_pool WHERE date=?",
            (yday_d,),
        ).fetchall() if yday_d else []
    except Exception:
        return []
    today_map = {r["symbol"]: r for r in today_rows}
    out: list[Signal] = []
    for y in yday_rows:
        if y["zhaban"]:
            continue
        ylb = int(y["lianban"] or 0)
        if ylb < 1:
            continue
        t = today_map.get(y["symbol"])
        t_zb = bool(t and t["zhaban"])
        t_lb = int(t["lianban"] or 0) if t else 0
        t_zt = bool(t) and not t_zb
        name = (t["name"] if t and t["name"] else None) or y["name"] or y["symbol"]
        if t_zt and t_lb == ylb + 1:
            out.append(Signal(
                id="lianban_promote", name="连板晋级", session=session,
                severity="action", subject_type="stock", subject=y["symbol"],
                hint=f"{name} 昨{ylb}板→今{t_lb}板",
                evidence={"from": ylb, "to": t_lb},
            ))
        elif t_zb or not t_zt:
            out.append(Signal(
                id="lianban_fail", name="连板断板", session=session,
                severity="watch", subject_type="stock", subject=y["symbol"],
                hint=f"{name} 昨{ylb}板今{'炸板' if t_zb else '未涨停'}",
                evidence={"from": ylb, "zhaban": int(t_zb)},
            ))
    return out


def rs_industry_leader_signals(conn) -> list[Signal]:
    """短线行业 RS 最新 rank≤8 且较上一 run_date 上升 ≥ RS_LEADER_UP。挂 daily。"""
    try:
        dates = [
            r["run_date"]
            for r in conn.execute(
                """SELECT DISTINCT run_date FROM quant_strength
                   WHERE period='short' AND obj_type='industry'
                   ORDER BY run_date DESC LIMIT 2"""
            )
        ]
    except Exception:
        return []
    if len(dates) < 2:
        return []
    today_d, yday_d = dates[0], dates[1]

    def _ranks(day: str) -> dict[str, int]:
        rows = conn.execute(
            """SELECT obj FROM quant_strength
               WHERE period='short' AND obj_type='industry' AND run_date=?
               ORDER BY rs DESC""",
            (day,),
        ).fetchall()
        return {r["obj"]: i + 1 for i, r in enumerate(rows)}

    today_r, yday_r = _ranks(today_d), _ranks(yday_d)
    out: list[Signal] = []
    for obj, rk in today_r.items():
        if rk > RS_LEADER_RANK:
            continue
        prev = yday_r.get(obj)
        if prev is None:
            continue
        if prev - rk >= RS_LEADER_UP:
            out.append(Signal(
                id="rs_industry_leader", name="行业RS跃升", session="daily",
                severity="watch", subject_type="sector", subject=obj,
                horizon="short", layer="discovery",
                hint=f"短线RS名次 {prev}→{rk}，进入前{RS_LEADER_RANK}",
                evidence={"rank": rk, "prev_rank": prev},
            ))
    return out
