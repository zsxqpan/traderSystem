"""报告生成（2026-08-15）：优化日报 + 盘中实时报告。

目标：可读性 + 交易指导性。
- 盘后日报（daily_report）：分节 + 交易指导（评级仓位/温度倾向/强度榜解读/候选池变化/持仓警戒）；
- 盘前清单（premarket_report）：当日关注 + 评级仓位 + 环境提示；
- 盘中实时报告（intraday_report）：核心关注实时行情 + 涨跌幅 + 持仓警戒 + 温度 + 评级仓位建议，
  供飞书/微信群艾特机器人时回复（feishu_group_watch.py 触发）。
"""
from __future__ import annotations

import datetime as dt

from invest.db import connect


# ---------- 通用小工具 ----------

def _pct(v) -> str:
    return f"{v:+.1%}" if v is not None else "-"


def _style_block(conn) -> str:
    """多指数风格/结构性行情判断（2026-08-16）：从 quant_strength(obj_type='index')
    读最新一期指数相对强度，渲染为报告小节。无数据返回空串。"""
    try:
        import pandas as _pd
        from invest.quant.style import style_to_text
        df = _pd.read_sql_query(
            """SELECT obj, rs, momentum, trend_stage, run_date
               FROM quant_strength WHERE obj_type='index'
               ORDER BY run_date DESC, rs DESC""", conn,
        )
        if df.empty:
            return ""
        latest = df["run_date"].iloc[0]
        df = df[df["run_date"] == latest]
        # 组装 style_result 结构（复用渲染函数，避免双份文案逻辑）
        style_result = {
            "run_date": latest,
            "index_strength": {},
            "style": {"ranking": []},
        }
        for _, r in df.iterrows():
            style_result["index_strength"][r["obj"]] = {
                "rs": r["rs"], "momentum": r["momentum"], "trend_stage": r["trend_stage"],
            }
        # 简化版风格文本（数据库里没有分组结论，重新算一次风格）
        from invest.quant.style import compute_style
        raw = _pd.read_sql_query(
            "SELECT date, index_code, close FROM index_bars ORDER BY date", conn,
        )
        closes = raw.pivot_table(index="date", columns="index_code", values="close")
        closes.index = _pd.to_datetime(closes.index, format="mixed", errors="coerce")
        closes = closes.dropna(how="all").sort_index()
        if "000300" not in closes.columns or len(closes) < 30:
            return style_to_text(style_result)
        full = compute_style(closes, closes["000300"].dropna())
        return style_to_text(full)
    except Exception:  # noqa: BLE001
        return ""


def _temp_guide(score: float | None) -> str:
    """温度 → 交易倾向（一句话指导）。"""
    if score is None:
        return "温度数据不足，维持中性仓位"
    if score >= 80:
        return "过热区：防回撤，减仓兑现，不追高"
    if score >= 60:
        return "偏暖：可持有/进攻，但控制单笔风险"
    if score >= 40:
        return "中性：正常仓位，等待结构性机会"
    return "偏冷：谨慎，低吸验证右侧信号"


def _rating_guide(conn) -> str:
    """评级 → 建议总仓位上限与倾向。"""
    from invest.discipline.rating import get_position_limit, get_rating
    lim = get_position_limit(conn)
    macro = (get_rating(conn, "macro") or {}).get("value", "未评")
    market = (get_rating(conn, "market") or {}).get("value", "未评")
    note = f"宏观{macro}·市场{market} → 建议总仓位上限 {lim:.0%}"
    if lim >= 0.4:
        note += "（可进攻）"
    elif lim >= 0.2:
        note += "（中性）"
    else:
        note += "（防守）"
    return note


def _strength_block(conn, period: str = "short", n: int = 5) -> str:
    """强度榜：短线轨带 RS5/10/20，中线轨只有 rs。"""
    from invest.pipeline import _top_strength
    return _top_strength(conn, period, n) or "-"


def _movers_block(conn, n: int = 5) -> str:
    from invest.pipeline import _daily_movers_block
    return _daily_movers_block(conn, n=n)


def _macro_text(conn) -> str:
    from invest.pipeline import _macro_text as _mt
    return _mt(conn) or "-"


def _freshness(conn) -> str:
    from invest.pipeline import _freshness as _f
    return _f(conn)


def _agent_viewpoints(conn, n: int = 6) -> str:
    from invest.pipeline import _agent_viewpoints as _av
    return _av(conn, n) or "-"


def _pool_delta(conn) -> str:
    """当日候选池变化：今日新入池 / 移出 / 等级变化 → 操作提示。"""
    today = dt.date.today().isoformat()
    added = conn.execute(
        "SELECT symbol, level FROM candidate_pool WHERE date(in_date)=? ORDER BY level",
        (today,),
    ).fetchall()
    removed = conn.execute(
        "SELECT symbol FROM candidate_pool WHERE date(out_date)=?",
        (today,),
    ).fetchall()
    parts = []
    if added:
        parts.append("新入池: " + "、".join(f"{r['symbol']}({r['level']})" for r in added))
    if removed:
        parts.append("移出: " + "、".join(r["symbol"] for r in removed))
    return "；".join(parts) if parts else "无"


def _card_alerts(conn, live_prices: dict | None = None) -> list[str]:
    """持仓卡片警戒：优先用实时价（盘中），无则用最新收盘价。

    返回格式 ["600519 破止损(现价1710<止损1720)", ...]。
    """
    rows = conn.execute(
        """SELECT symbol, level, cycle, status, stop_loss, target FROM cards
           WHERE status IN ('locked','review') ORDER BY level"""
    ).fetchall()
    out: list[str] = []
    for r in rows:
        sym = r["symbol"]
        price = (live_prices or {}).get(sym)
        if price is None:
            row = conn.execute(
                "SELECT close FROM daily_bars WHERE symbol=? ORDER BY REPLACE(date,'-','') DESC LIMIT 1",
                (sym,),
            ).fetchone()
            price = float(row["close"]) if row else None
        if price is None:
            continue
        stop, target = r["stop_loss"], r["target"]
        if stop is not None and price <= float(stop):
            out.append(f"{sym} 破止损(现价{price:.2f}<止损{stop:.2f})")
        elif stop is not None and price <= float(stop) * 1.03:
            out.append(f"{sym} 近止损(现价{price:.2f})")
        elif target is not None and price >= float(target) * 0.97:
            out.append(f"{sym} 近目标(现价{price:.2f}≥{target:.2f})")
    return out


def _latest_close_map(conn, symbols: list[str]) -> dict[str, float]:
    """标的 → 最新收盘价（用于实时涨跌幅基准）。"""
    if not symbols:
        return {}
    rows = conn.execute(
        """SELECT d.symbol, d.close FROM daily_bars d
           JOIN (SELECT symbol, MAX(REPLACE(date,'-','')) md FROM daily_bars
                 WHERE symbol IN (%s) GROUP BY symbol) m
           ON d.symbol=m.symbol AND REPLACE(d.date,'-','')=m.md""" % ",".join("?" * len(symbols)),
        symbols,
    ).fetchall()
    return {r["symbol"]: float(r["close"]) for r in rows}


def _live_quotes(db_path: str, core: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """盘中实时行情：返回 (live_price, pct_map)；三源失败返回空表（由调用方回退）。"""
    if not core:
        return {}, {}
    try:
        from invest.intraday import fetch_batch_prices
        live = fetch_batch_prices(core, db_path=db_path)
    except Exception:  # noqa: BLE001
        return {}, {}
    conn = connect(db_path)
    try:
        latest_close = _latest_close_map(conn, core)
    finally:
        conn.close()
    pct_map: dict[str, float] = {}
    for sym in core:
        price = live.get(sym)
        base = latest_close.get(sym)
        if price is not None and base:
            pct_map[sym] = price / float(base) - 1
    return live, pct_map


# ---------- 盘后日报（优化版） ----------

def daily_report(db_path: str, agent_text: str = "") -> str:
    """盘后日报：结构分节 + 交易指导（评级仓位/温度倾向/强度榜解读/候选池变化/持仓警戒）。"""
    conn = connect(db_path)
    try:
        new_vp = conn.execute(
            "SELECT COUNT(*) AS n FROM viewpoints WHERE date(created_at)=date('now','localtime')"
        ).fetchone()["n"]
        stop_hits = conn.execute(
            """SELECT COUNT(*) AS n FROM trade_records
               WHERE date(created_at)=date('now','localtime') AND deviation_note LIKE '%止损%'"""
        ).fetchone()["n"]
        temp_row = conn.execute(
            "SELECT score, profit_effect FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        score = float(temp_row["score"]) if temp_row else None
        width = float(temp_row["profit_effect"]) if temp_row and temp_row["profit_effect"] is not None else None
        card_alerts = _card_alerts(conn)
        pool_delta = _pool_delta(conn)
        guide = _temp_guide(score)
        rating_guide = _rating_guide(conn)

        lines = []
        lines.append("【A股投资系统 · 盘后日报】")
        lines.append(f"数据截至: {_freshness(conn)}")
        lines.append("")
        lines.append("📊 市场温度: " + (
            f"{score:.0f}/100" + (f" | 宽度{width:.0%}" if width is not None else "") if score is not None else "暂无"
        ))
        lines.append(f"   → {guide}")
        lines.append(f"🎯 仓位: {rating_guide}")
        lines.append(f"📈 评级: {_ratings(conn)}")
        # 市场风格/结构性行情（2026-08-16 多指数分析）
        style_txt = _style_block(conn)
        if style_txt:
            lines.append("")
            lines.append(style_txt)
        # 情绪周期（2026-08-16，quantdash 借鉴）：涨停/连板/炸板 → 四阶段 + 操作基调
        try:
            import pandas as _pd
            from invest.quant.emotion_cycle import emotion_cycle
            emo = _pd.read_sql_query(
                "SELECT date, limit_up_count, max_lianban, zhaban_rate FROM market_emotion ORDER BY date", conn,
            )
            cyc = emotion_cycle(emo) if not emo.empty else {"stage": "数据不足"}
            lines.append(f"🔥 情绪周期: {cyc['stage']}")
            if cyc.get("reasons"):
                lines.append(f"   → {'；'.join(cyc['reasons'])}")
            lines.append(f"   → {cyc.get('guide', '')}")
        except Exception:  # noqa: BLE001
            pass
        lines.append("")
        lines.append("【当日板块】")
        lines.append(_movers_block(conn))
        lines.append("")
        lines.append("【短线强度前5 (RS 5/10/20日超额)】")
        lines.append(_strength_block(conn, "short"))
        lines.append("")
        lines.append("【中线强度前3】")
        lines.append(_strength_block(conn, "mid", 3))
        lines.append("")
        if pool_delta and pool_delta != "无":
            lines.append("【候选池变化】")
            lines.append(pool_delta)
            lines.append("")
        if card_alerts:
            lines.append("⚠️ 持仓警戒:")
            lines += [f"  - {a}" for a in card_alerts]
            lines.append("")
        lines.append(f"【今日】新增观点 {new_vp} 条 | 触发止损 {stop_hits} 笔")
        lines.append("【Agent 复盘】")
        lines.append(_agent_viewpoints(conn) if agent_text == "" else (agent_text or "-"))
        msg = "\n".join(lines)
    finally:
        conn.close()
    return msg


def premarket_report(db_path: str, agent_text: str = "") -> str:
    """盘前清单：当日关注 + 评级仓位 + 环境提示（含环境重评触发）。"""
    conn = connect(db_path)
    try:
        env_notes = ""
        try:
            from invest.discipline.macro_gate import check_env_retrigger
            env = check_env_retrigger(conn)
            if env["triggers"]:
                env_notes = "\n[环境重评] " + "\n  ".join(env["triggers"])
        except Exception:  # noqa: BLE001
            pass
        temp_row = conn.execute(
            "SELECT score FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        score = float(temp_row["score"]) if temp_row else None
        lines = []
        lines.append("【A股投资系统 · 盘前】")
        lines.append(f"数据截至: {_freshness(conn)}")
        lines.append(f"🎯 仓位: {_rating_guide(conn)}")
        lines.append(f"📈 评级: {_ratings(conn)}")
        if score is not None:
            lines.append(f"🌡️ 温度: {score:.0f}/100 → {_temp_guide(score)}")
        # 市场风格/结构性行情（2026-08-16 多指数分析）
        style_txt = _style_block(conn)
        if style_txt:
            lines.append("")
            lines.append(style_txt)
        if env_notes:
            lines.append(env_notes)
        lines.append("")
        lines.append("【关注方向 (Agent)】")
        lines.append(agent_text or "[Agent 未运行]")
        msg = "\n".join(lines)
    finally:
        conn.close()
    return msg


def _ratings(conn) -> str:
    from invest.pipeline import _ratings_block
    return _ratings_block(conn)


# ---------- 盘中实时报告 ----------

def intraday_report(db_path: str) -> str:
    """盘中实时报告：核心关注实时行情 + 涨跌幅 + 持仓警戒 + 温度 + 评级仓位。

    供飞书/微信群艾特机器人时回复；非交易时段也生成（数据为最近快照）。
    """
    conn = connect(db_path)
    try:
        core = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level IN ('core','track') AND out_date IS NULL ORDER BY level"
        )]
        temp_row = conn.execute(
            "SELECT score, profit_effect FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        score = float(temp_row["score"]) if temp_row else None
        width = float(temp_row["profit_effect"]) if temp_row and temp_row["profit_effect"] is not None else None
    finally:
        conn.close()

    lines = []
    lines.append(f"【盘中实时报告 · {dt.datetime.now().strftime('%H:%M')}】")
    conn0 = connect(db_path)
    try:
        freshness = _freshness(conn0)
    finally:
        conn0.close()
    lines.append(f"数据截至: {freshness}")

    # 实时行情（三源轮询，仅收新鲜数据）
    live, pct_map = _live_quotes(db_path, core)

    conn = connect(db_path)
    try:
        if live:
            lines.append("")
            lines.append("【核心关注实时行情】")
            for sym in core:
                price = live.get(sym)
                if price is None:
                    continue
                pct = pct_map.get(sym)
                lines.append(f"  {sym}  {price:.2f}  {_pct(pct)}")
            hot = sorted(((s, p) for s, p in pct_map.items() if p is not None), key=lambda x: -abs(x[1]))[:3]
            if hot:
                lines.append("")
                lines.append("【异动最大】")
                for sym, p in hot:
                    lines.append(f"  {sym} {_pct(p)}")
        else:
            lines.append("")
            lines.append("⚠️ 实时行情暂不可用（三源失败或非交易时段），以下为最近收盘数据")
            if core:
                latest_close = _latest_close_map(conn, core)
                for sym in core:
                    if sym in latest_close:
                        pct_map[sym] = 0.0
                        lines.append(f"  {sym}  收盘 {latest_close[sym]:.2f}")

        # 持仓警戒（盘中用实时价）
        card_alerts = _card_alerts(conn, live_prices=live)
        # 温度与仓位指导
        lines.append("")
        lines.append("📊 温度: " + (
            f"{score:.0f}/100" + (f" | 宽度{width:.0%}" if width is not None else "") if score is not None else "暂无"
        ) + f" → {_temp_guide(score)}")
        lines.append(f"🎯 仓位: {_rating_guide(conn)}")
        # 市场风格/结构性行情（2026-08-16 多指数分析）
        style_txt = _style_block(conn)
        if style_txt:
            lines.append("")
            lines.append(style_txt)
        if card_alerts:
            lines.append("")
            lines.append("⚠️ 持仓警戒:")
            lines += [f"  - {a}" for a in card_alerts]
    finally:
        conn.close()
    lines.append("")
    lines.append("（数据失效即防守：行情不新鲜时不作 P0 决策）")
    return "\n".join(lines)
