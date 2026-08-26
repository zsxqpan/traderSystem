"""报告生成（2026-08-15）：优化日报 + 盘中实时报告。

目标：可读性 + 交易指导性。
- 盘后日报（daily_report）：分节 + 交易指导（评级仓位/温度倾向/强度榜解读/候选池变化/持仓警戒）；
- 盘前清单（premarket_report）：当日关注 + 评级仓位 + 环境提示；
- 盘中实时报告（intraday_report）：核心关注实时行情 + 涨跌幅 + 持仓警戒 + 温度 + 评级仓位建议，
  供飞书群 @机器人 时回复（invest/push/feishu_ws.py 触发，随 scripts/run_service.py 常驻）。
"""
from __future__ import annotations

import datetime as dt
import logging

from invest.db import connect

logger = logging.getLogger(__name__)


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
    except Exception:
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


# ---------- 短线操作辅助（2026-08-16：聚焦盘面变化/异常波动做T/建仓时机） ----------

def _abnormal_moves(conn, n: int = 5) -> list[dict]:
    """异常波动检测（做 T / 短线机会信号）：从 daily_bars 最新两个交易日找：
    - 量比突变：最新日成交量 / 前 5 日均量 > 2.0；
    - 振幅放大：最新日 (high-low)/close > 6%；
    - 长上影/长下影：影线占振幅 > 60%。
    返回 [{symbol, signal, detail}]，仅限候选池标的（防噪音）。
    """
    rows = conn.execute(
        """SELECT symbol, date, open, high, low, close, volume FROM daily_bars
           ORDER BY symbol, REPLACE(date,'-','') DESC LIMIT 10000"""
    ).fetchall()
    if not rows:
        return []
    # 按标的分组取最近 6 日
    by_sym: dict[str, list] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(dict(r))
    out: list[dict] = []
    for sym, bars in by_sym.items():
        if len(bars) < 6:
            continue
        bars = sorted(bars, key=lambda x: x["date"])[-6:]
        latest, prev5 = bars[-1], bars[:-1]
        avg_vol = sum(float(b["volume"]) for b in prev5 if b["volume"]) / max(1, len([b for b in prev5 if b["volume"]]))
        vol = float(latest["volume"] or 0)
        close = float(latest["close"] or 0)
        high, low, open_ = float(latest["high"] or 0), float(latest["low"] or 0), float(latest["open"] or 0)
        if close <= 0 or avg_vol <= 0:
            continue
        signals: list[str] = []
        # 量比
        vol_ratio = vol / avg_vol
        if vol_ratio >= 2.0:
            signals.append(f"量比{vol_ratio:.1f}（放量{vol_ratio:.0f}倍）")
        # 振幅
        amp = (high - low) / close if close else 0
        if amp >= 0.06:
            signals.append(f"振幅{amp:.1%}")
        # 影线（长上影=抛压 / 长下影=承接）
        if amp >= 0.04:
            upper_shadow = (high - max(open_, close)) / (high - low) if high > low else 0
            lower_shadow = (min(open_, close) - low) / (high - low) if high > low else 0
            if upper_shadow >= 0.6:
                signals.append("长上影（抛压重，逢高减）")
            elif lower_shadow >= 0.6:
                signals.append("长下影（有承接，可低吸试错）")
        if signals:
            out.append({"symbol": sym, "signal": "；".join(signals),
                        "detail": f"收{close:.2f} 高{high:.2f} 低{low:.2f} 量{vol/1e4:.0f}万"})
    return out[:n]


def _t_trade_hints(conn, live: dict[str, float], pct_map: dict[str, float]) -> list[str]:
    """做 T 提示（针对持仓/候选标的，盘中用实时价）：
    - 振幅大 + 现价接近当日低点 → 低吸做 T；
    - 现价远离日内高点回落 → 高抛做 T；
    - 急涨远离均价 → 高抛（用最新收盘近似均价）。
    返回提示列表。
    """
    hints: list[str] = []
    for sym, price in live.items():
        pct = pct_map.get(sym)
        if price is None or pct is None:
            continue
        # 用最近收盘价近似当日均价（数据有限时的粗判）
        row = conn.execute(
            "SELECT high, low, open, close FROM daily_bars WHERE symbol=? "
            "ORDER BY REPLACE(date,'-','') DESC LIMIT 1", (sym,),
        ).fetchone()
        if not row:
            continue
        high, low = float(row["high"] or price), float(row["low"] or price)
        day_range = high - low
        if day_range <= 0:
            continue
        pos = (price - low) / day_range  # 0=日内最低 1=日内最高
        # 现价贴近日内低点 + 有下影/支撑 → 低吸做 T 候选
        if pos < 0.15 and abs(pct) < 0.05:
            hints.append(f"{sym} 现价贴近日内低点（位置{pos:.0%}），若承接可低吸做 T")
        # 现价接近日内高点 + 涨幅大 → 高抛做 T 候选
        elif pos > 0.85 and pct > 0.02:
            hints.append(f"{sym} 现价高位（位置{pos:.0%}，涨{pct:+.1%}），可高抛做 T")
    return hints[:5]


def _entry_timing_hints(conn) -> list[str]:
    """建仓时机提示（短线聚焦）：
    - 情绪周期非退潮 + 温度中性偏暖 + 强度榜启动/加速 → 提示关注；
    - 候选池中估值分位低 + 强度转强 → 提示建仓窗口。
    """
    hints: list[str] = []
    try:
        import pandas as _pd

        from invest.quant.emotion_cycle import emotion_cycle
        emo = _pd.read_sql_query(
            "SELECT date, limit_up_count, max_lianban, zhaban_rate FROM market_emotion ORDER BY date", conn,
        )
        cyc = emotion_cycle(emo) if not emo.empty else {"stage": "数据不足"}
        stage = cyc["stage"]
        if stage in ("启动", "主升"):
            hints.append(f"情绪周期「{stage}」：短线可积极，关注首板/龙头")
        elif stage == "冰点":
            hints.append("情绪周期「冰点」：短线观望，等反包/回暖信号")
        elif stage == "退潮":
            hints.append("情绪周期「退潮」：不接力高位，防守为主")
    except Exception:
        pass
    try:
        temp = conn.execute(
            "SELECT score FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        if temp and temp["score"] is not None:
            s = float(temp["score"])
            if 40 <= s < 70:
                hints.append(f"温度{s:.0f}（中性偏暖）：正常仓，回调低吸机会")
    except Exception:
        pass
    # 候选池低估值 + 强度转强
    try:
        rows = conn.execute(
            """SELECT cp.symbol, cp.industry FROM candidate_pool cp
               WHERE cp.out_date IS NULL LIMIT 10"""
        ).fetchall()
        for r in rows:
            ind = r["industry"] or ""
            if not ind:
                continue
            row = conn.execute(
                """SELECT pe_pct, crowding_state FROM quant_valuation WHERE obj=?
                   ORDER BY run_date DESC LIMIT 1""", (ind,),
            ).fetchone()
            srow = conn.execute(
                """SELECT trend_stage, rs FROM quant_strength WHERE obj_type='industry'
                   AND obj=? ORDER BY run_date DESC LIMIT 1""", (ind,),
            ).fetchone()
            if row and srow and row["pe_pct"] is not None and float(row["pe_pct"]) < 0.3 \
               and srow["trend_stage"] in ("启动", "加速"):
                hints.append(f"{r['symbol']}({ind}) 低估值+强度启动：建仓窗口候选")
    except Exception:
        pass
    return hints[:5]


def _live_quotes(db_path: str, core: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """盘中实时行情：返回 (live_price, pct_map)；三源失败返回空表（由调用方回退）。"""
    if not core:
        return {}, {}
    try:
        from invest.intraday import fetch_batch_prices
        live = fetch_batch_prices(core, db_path=db_path)
    except Exception:
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


def _focus_industries_block(conn, db_path: str) -> str:
    """重点关注行业（2026-08-18 方案C）：名单内行业四维度数据 + LLM 一句话意见。

    名单来自配置 focus_industries（.env FOCUS_INDUSTRIES，逗号分隔，随周报等信息调整）。
    数据：中线 RS/趋势 + 估值分位/拥挤度 + 资金风格；意见由 LLM 一次调用汇总（job='daily_report'，
    受日预算约束），失败/未配置时只出数据不出意见。
    """
    from invest.config import get_settings

    focus = [x.strip() for x in (getattr(get_settings(), "focus_industries", "") or "").split(",") if x.strip()]
    if not focus:
        return ""
    lines: list[str] = []
    data_lines: list[str] = []
    for ind in focus:
        parts = [ind]
        try:
            row = conn.execute(
                """SELECT rs, trend_stage FROM quant_strength
                   WHERE period='mid' AND obj_type='industry' AND obj=?
                     AND run_date=(SELECT MAX(run_date) FROM quant_strength
                                   WHERE period='mid' AND obj_type='industry' AND obj=?)
                   ORDER BY run_date DESC LIMIT 1""", (ind, ind),
            ).fetchone()
            if row:
                parts.append(f"RS {float(row['rs']):+.1%} [{row['trend_stage']}]")
        except Exception:
            pass
        try:
            val = conn.execute(
                """SELECT pe_pct, crowding_state FROM quant_valuation
                   WHERE obj=? ORDER BY run_date DESC LIMIT 1""", (ind,),
            ).fetchone()
            if val and val["pe_pct"] is not None:
                parts.append(f"PE分位{float(val['pe_pct']):.0%}")
                if val["crowding_state"]:
                    parts.append(f"拥挤:{val['crowding_state']}")
        except Exception:
            pass
        try:
            cap = conn.execute(
                """SELECT style FROM quant_capital WHERE obj_type='industry' AND obj=?
                   ORDER BY run_date DESC LIMIT 1""", (ind,),
            ).fetchone()
            if cap and cap["style"]:
                parts.append(f"资金:{cap['style']}")
        except Exception:
            pass
        data_lines.append("  " + "｜".join(parts))
    if not data_lines:
        return ""
    lines.append("【重点关注行业】")
    lines.extend(data_lines)
    # LLM 一句话意见（一次调用汇总，省 token；预算内 job='daily_report'）
    try:
        from invest.agent.llm import LLMClient
        from invest.config import get_settings as _gs

        settings = _gs()
        if settings.llm_api_key:
            client = LLMClient(conn=conn, settings=settings)
            prompt = (
                "以下是重点关注行业的最新数据：\n" + "\n".join(data_lines) +
                f"\n请对每个行业给一句话操作意见（结合估值/强度/资金，Serenity 机构思维："
                f"护城河/景气/估值分位），格式『行业：意见』，共 {len(focus)} 行，不要编造数据。"
            )
            out = client.run(
                system="你是 A 股行业研究员（机构级投资思维：护城河/景气度/估值分位）。",
                user=prompt, job="daily_report", max_turns=1,
            )
            if out and not out.startswith("[预算不足"):
                lines.append("【重点行业意见】")
                lines.append(out.strip())
    except Exception as exc:
        logger.warning("重点行业 LLM 意见失败: %s", exc)
    return "\n".join(lines)


# ---------- 盘后日报（优化版） ----------

def daily_report(db_path: str, agent_text: str = "") -> str:
    """盘后日报（2026-08-18 方案C 详细版）：从宏观到微观。

    顺序：宏观流动性 → 市场温度/仓位/评级 → 风格 → 情绪周期 → 当日板块 →
    重点关注行业（名单+LLM意见）→ 短线强度 → 异常波动/建仓 → 中线强度 →
    候选池变化 → 消息面（大模型提炼，微观）→ 持仓警戒 → 今日统计 → Agent 复盘。
    """
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
        # 宏观流动性（2026-08-18 方案C：从重要宏观开始）
        try:
            from invest.pipeline import _macro_text

            macro = _macro_text(conn)
            if macro and macro != "-":
                lines.append("")
                lines.append("【宏观】" + macro)
        except Exception:
            pass
        lines.append("")
        lines.append("📊 市场温度: " + (
            f"{score:.0f}/100" + (f" | 宽度{width:.0%}" if width is not None else "") if score is not None else "暂无"
        ))
        lines.append(f"   → {guide}")
        lines.append(f"🎯 仓位: {rating_guide}")
        lines.append(f"📈 评级: {_ratings(conn)}")
        # 市场风格/结构性行情
        style_txt = _style_block(conn)
        if style_txt:
            lines.append("")
            lines.append(style_txt)
        # 情绪周期
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
        except Exception:
            pass
        lines.append("")
        lines.append("【当日板块】")
        lines.append(_movers_block(conn))
        lines.append("")
        # 重点关注行业（2026-08-18 方案C）
        focus_block = _focus_industries_block(conn, db_path)
        if focus_block:
            lines.append("")
            lines.append(focus_block)
            lines.append("")
        lines.append("【短线强度前5 (RS 5/10/20日超额)】")
        lines.append(_strength_block(conn, "short"))
        lines.append("")

        # === 短线操作辅助（2026-08-16 新增）===
        abnormal = _abnormal_moves(conn)
        if abnormal:
            lines.append("⚡ 异常波动（做T/短线信号）:")
            for a in abnormal:
                lines.append(f"  - {a['symbol']} {a['signal']}（{a['detail']}）")
            lines.append("")
        entry = _entry_timing_hints(conn)
        if entry:
            lines.append("🎯 建仓时机提示:")
            lines += [f"  - {e}" for e in entry]
            lines.append("")

        lines.append("【中线强度前3】")
        lines.append(_strength_block(conn, "mid", 3))
        lines.append("")
        if pool_delta and pool_delta != "无":
            lines.append("【候选池变化】")
            lines.append(pool_delta)
            lines.append("")
        # 消息面（大模型提炼，微观——2026-08-18 方案C）
        news_block = _news_block(db_path, n=4, days=2, job="daily_report")
        if news_block:
            lines.append("【消息面 · 大模型提炼（近2日）】")
            lines.append(news_block)
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
        except Exception:
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


# ---------- 周报（2026-08-16：聚焦中期） ----------

def _fetch_telegraph_lines(days: int = 3) -> list[str]:
    """拉取财联社电报近 days 天原始素材（时间 | 标题/内容前120字）。失败返回空列表。"""
    try:
        import akshare as ak

        df = ak.stock_info_global_cls()
        if df is None or df.empty or "标题" not in df.columns:
            return []
        df = df.dropna(subset=["发布时间"])
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        try:
            df = df[df["发布日期"].astype(str).str[:10] >= cutoff]
        except Exception:
            pass
        rows = df.sort_values("发布时间", ascending=False).head(40)
        out: list[str] = []
        for _, r in rows.iterrows():
            title = (r["标题"] or "").strip()
            content = (r["内容"] or "").strip().replace("\n", " ") if "内容" in df.columns else ""
            text = title or content[:80]
            if not text:
                continue
            out.append(f"{str(r['发布时间'])[:16]} | {text[:120]}")
        return out
    except Exception:
        return []


def _news_block(db_path: str, n: int = 5, days: int = 3, job: str = "weekly") -> str:
    """消息面：**大模型提炼**市场讨论度最高/最重要的消息（2026-08-18 按用户要求改造）。

    素材=财联社电报（akshare）近 days 天；由 LLM 判断"哪条重要/讨论度高"并给出
    一句话理由（DeepSeek API 无联网检索，素材仍取电报，但筛选/提炼由大模型完成）。
    LLM 失败/未配置/预算不足 → 兜底直接列素材，报告不阻断。
    """
    raw_lines = _fetch_telegraph_lines(days)
    if not raw_lines:
        return "（近几日暂无消息面素材）"
    try:
        from invest.agent.llm import LLMClient
        from invest.config import get_settings
        from invest.db import connect

        settings = get_settings()
        if settings.llm_api_key:
            conn = connect(db_path)
            try:
                client = LLMClient(conn=conn, settings=settings)
                sys_prompt = (
                    "你是财经新闻编辑。从下面财联社电报素材中，挑选市场讨论度最高/最重要的"
                    f"{n} 条。要求：\n"
                    "- 每条输出一行：『主题｜一句话理由（为什么重要/讨论度高）』；\n"
                    "- 优先宏观政策、行业大事件、龙头公司异动；忽略重复/营销类内容；\n"
                    "- 素材不足或都无关紧要时，如实说明'本周无特别重要消息'，不要编造；\n"
                    "- 控制在 {n} 行以内，不要复述原文。"
                )
                material = "\n".join(raw_lines)[:6000]
                out = client.run(system=sys_prompt, user=material, job=job, max_turns=1)
                if out and not out.startswith("[预算不足"):
                    return out.strip()
            finally:
                conn.close()
    except Exception as exc:
        logger.warning("消息面 LLM 提炼失败，回退直列: %s", exc)
    # 兜底：直列素材
    return "\n".join(f"  - {ln}" for ln in raw_lines[:n])


def weekly_report(db_path: str, agent_text: str = "") -> str:
    """周报：聚焦中期（中线强度/行业趋势/估值分位/宏观流动性/评级仓位/持仓中线评估）。

    与日报（短线操作辅助）分工：周报看方向与配置，日报看短线机会。
    2026-08-18 增：消息面（财联社电报近 7 日），周日 20:00 随周报一起发送。
    """
    conn = connect(db_path)
    try:
        temp_row = conn.execute(
            "SELECT score FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        score = float(temp_row["score"]) if temp_row else None
        # 中线强度前 8（含趋势阶段）
        mid_rows = conn.execute(
            """SELECT obj, rs, trend_stage FROM quant_strength
               WHERE period='mid' AND obj_type='industry'
                 AND run_date = (SELECT MAX(run_date) FROM quant_strength
                                 WHERE period='mid' AND obj_type='industry')
               ORDER BY rs DESC LIMIT 8"""
        ).fetchall()
        mid_block = "\n".join(
            f"  {r['obj']} rs{r['rs']:+.1%} [{r['trend_stage']}]" for r in mid_rows
        ) or "-"
        # 估值分位（PE/PB）行业 TOP（低估值 + 趋势好的中线候选）
        val_rows = conn.execute(
            """SELECT v.obj, v.pe_pct, v.crowding_state, s.trend_stage, s.rs
               FROM quant_valuation v
               JOIN (SELECT obj, trend_stage, rs FROM quant_strength
                     WHERE period='mid' AND obj_type='industry'
                       AND run_date=(SELECT MAX(run_date) FROM quant_strength
                                     WHERE period='mid' AND obj_type='industry')) s
                 ON v.obj = s.obj
               WHERE v.pe_pct IS NOT NULL
                 AND v.run_date = (SELECT MAX(run_date) FROM quant_valuation WHERE obj=v.obj)
               ORDER BY v.pe_pct ASC LIMIT 6"""
        ).fetchall()
        val_block = "\n".join(
            f"  {r['obj']} PE分位{r['pe_pct']:.0%} [{r['trend_stage']} rs{r['rs']:+.1%}]" for r in val_rows
        ) or "-"
        lines = []
        lines.append("【A股投资系统 · 周报】")
        lines.append(f"数据截至: {_freshness(conn)}")
        lines.append("")
        lines.append(f"📈 评级: {_ratings(conn)}")
        lines.append(f"🎯 仓位: {_rating_guide(conn)}")
        if score is not None:
            lines.append(f"🌡️ 温度: {score:.0f}/100 → {_temp_guide(score)}")
        lines.append("")
        lines.append("【中线强度前8（周线口径，看方向）】")
        lines.append(mid_block)
        lines.append("")
        lines.append("【低估值+趋势候选（中线配置视角）】")
        lines.append(val_block)
        lines.append("")
        lines.append("【宏观流动性】")
        lines.append(_macro_text(conn))
        lines.append("")
        # 2026-08-23 角度 skill 复用：周期行业定位（d30），失败静默不阻断
        try:
            from invest.skills.sections.d30_cycle_position import _cycle_position

            cyc = _cycle_position(conn)
            if cyc:
                lines.append(cyc)
                lines.append("")
        except Exception:
            pass
        lines.append("【消息面 · 大模型提炼（近3日）】")
        lines.append(_news_block(db_path))
        lines.append("")
        lines.append("【周度观点】")
        lines.append(_agent_viewpoints(conn) if agent_text == "" else (agent_text or "-"))
        msg = "\n".join(lines)
    finally:
        conn.close()
    return msg


def _emotion_block(conn) -> str:
    """情绪·人气：最新交易日涨停/最高连板/炸板率 + 情绪周期阶段。无数据返回空串。"""
    try:
        import pandas as _pd

        from invest.quant.emotion_cycle import emotion_cycle

        emo = _pd.read_sql_query(
            "SELECT date, limit_up_count, max_lianban, zhaban_rate FROM market_emotion ORDER BY date", conn,
        )
        if emo.empty:
            return ""
        cyc = emotion_cycle(emo)
        last = emo.iloc[-1]
        parts = []
        lu = last.get("limit_up_count")
        ml = last.get("max_lianban")
        zr = last.get("zhaban_rate")
        if lu is not None and not _pd.isna(lu):
            parts.append(f"涨停{float(lu):.0f}")
        if ml is not None and not _pd.isna(ml):
            parts.append(f"最高连板{float(ml):.0f}")
        if zr is not None and not _pd.isna(zr):
            parts.append(f"炸板率{float(zr):.0%}")
        parts.append(f"情绪:{cyc['stage']}")
        return " ".join(parts)
    except Exception:
        return ""


def _sector_moves_block(conn, n: int = 3) -> str:
    """板块异动：最新交易日行业涨幅 TOP n（industry_bars 收盘口径，方向参考）。"""
    try:
        rows = conn.execute(
            """SELECT t.industry, t.close, p.close AS prev
               FROM industry_bars t
               JOIN industry_bars p ON p.industry = t.industry
                 AND p.date = (SELECT MAX(date) FROM industry_bars
                               WHERE industry=t.industry AND date < t.date)
               WHERE t.date = (SELECT MAX(date) FROM industry_bars)
                 AND t.close IS NOT NULL AND p.close IS NOT NULL AND p.close > 0
               ORDER BY (t.close / p.close - 1) DESC LIMIT ?""",
            (n,),
        ).fetchall()
        if not rows:
            return ""
        return "\n".join(
            f"  {r['industry']}  {(r['close'] / r['prev'] - 1):+.2%}" for r in rows
        )
    except Exception:
        return ""


def _capital_leaders_block(conn, n: int = 3) -> str:
    """资金焦点·龙头人气：最新龙虎榜净买入 TOP n。"""
    try:
        rows = conn.execute(
            """SELECT symbol, name, net FROM dragon_tiger
               WHERE date = (SELECT MAX(date) FROM dragon_tiger) AND net IS NOT NULL
               ORDER BY net DESC LIMIT ?""",
            (n,),
        ).fetchall()
        if not rows:
            return ""
        return "\n".join(
            f"  {r['symbol']} {r['name'] or ''} 净买{float(r['net'])/1e8:.2f}亿" for r in rows
        )
    except Exception:
        return ""


def _action_guide(conn, score: float | None) -> str:
    """今日操作建议（2026-08-18 方案A）：温度 + 情绪周期 → 一句话操作倾向。"""
    try:
        import pandas as _pd

        from invest.quant.emotion_cycle import emotion_cycle

        emo = _pd.read_sql_query(
            "SELECT date, limit_up_count, max_lianban, zhaban_rate FROM market_emotion ORDER BY date", conn,
        )
        stage = emotion_cycle(emo)["stage"] if not emo.empty else "数据不足"
    except Exception:
        stage = "数据不足"
    if score is None:
        temp_txt = "温度数据不足"
    elif score >= 80:
        temp_txt = "过热防回撤"
    elif score >= 60:
        temp_txt = "偏暖可进攻"
    elif score >= 40:
        temp_txt = "中性等机会"
    else:
        temp_txt = "偏冷宜防守"
    acts = {
        "冰点": "低吸为主，控制仓位，等情绪回暖",
        "启动": "可积极，关注首板/龙头",
        "主升": "持仓为主，避免追高，留意退潮信号",
        "退潮": "减仓兑现，不接飞刀",
    }
    act = acts.get(stage, "中性操作，按计划执行")
    return f"{temp_txt}｜情绪{stage} → {act}"


def _limit_up_ladder_block(conn, n: int = 6) -> str:
    """连板梯队/涨停龙头（2026-08-20，东财涨停池个股明细，盘中实时）。

    取最新日期 limit_up_pool：按连板数降序 TOP n（含炸板标记）；无数据返回空串。
    """
    try:
        rows = conn.execute(
            """SELECT symbol, name, lianban, seal_amount, zhaban FROM limit_up_pool
               WHERE date = (SELECT MAX(date) FROM limit_up_pool) AND zhaban=0
               ORDER BY lianban DESC, seal_amount DESC NULLS LAST LIMIT ?""",
            (n,),
        ).fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            tag = f"{int(r['lianban'])}板" if r["lianban"] else "首板"
            seal = f" 封单{float(r['seal_amount'])/1e4:.1f}万" if r["seal_amount"] is not None else ""
            lines.append(f"  {r['symbol']} {r['name'] or ''} {tag}{seal}")
        return "\n".join(lines)
    except Exception:
        return ""


def _fund_line_block(conn, n: int = 3) -> str:
    """资金主线：行业板块主力净流入 TOP n（2026-08-20，东财行业资金流）。"""
    try:
        rows = conn.execute(
            """SELECT industry, main_net, main_net_pct FROM sector_fund_flow
               WHERE date = (SELECT MAX(date) FROM sector_fund_flow)
               ORDER BY main_net DESC LIMIT ?""",
            (n,),
        ).fetchall()
        if not rows:
            return ""
        return "\n".join(
            f"  {r['industry']}  主力净流入{float(r['main_net'])/1e8:+.2f}亿"
            + (f"（占比{float(r['main_net_pct']):+.1f}%）" if r["main_net_pct"] is not None else "")
            for r in rows
        )
    except Exception:
        return ""


# ---------- 盘中实时报告 ----------

def intraday_report(db_path: str, public: bool = False, brief: bool = True) -> str:
    """盘中实时报告（2026-08-18 v3：默认简洁版 + 今日操作建议）。

    - brief=True（默认）：核心池行情 + 情绪人气 + 板块异动 + 龙虎榜龙头 + 温度/仓位 + 今日操作建议；
      不含 做T/建仓/持仓警戒 等细节（私聊/群聊默认都用简洁版，明确要"详细/完整"才发完整版）；
    - brief=False：完整版，额外含 做T提示 / 异动最大 / 建仓时机 / 持仓警戒（public=False 时）；
    - public=True（非管理员）：不含持仓警戒等私有持仓信息。
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
            if not brief:
                # 做 T 提示（实时价日内位置）——完整版
                t_hints = _t_trade_hints(conn, live, pct_map)
                if t_hints:
                    lines.append("")
                    lines.append("🔄 做 T 提示:")
                    lines += [f"  - {h}" for h in t_hints]
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

        if not brief:
            # 建仓时机提示（短线聚焦）——完整版
            entry = _entry_timing_hints(conn)
            if entry:
                lines.append("")
                lines.append("🎯 建仓时机:")
                lines += [f"  - {e}" for e in entry]

        # 情绪·人气（2026-08-18：替代指数信息，聚焦板块/龙头/人气）
        emo = _emotion_block(conn)
        if emo:
            lines.append("")
            lines.append(f"🔥 情绪·人气: {emo}")
        # 连板梯队/涨停龙头（2026-08-20，盘中实时）
        ladder = _limit_up_ladder_block(conn)
        if ladder:
            lines.append("")
            lines.append("【连板梯队·涨停龙头】")
            lines.append(ladder)
        # 资金主线（2026-08-20，行业主力净流入）
        fund = _fund_line_block(conn)
        if fund:
            lines.append("")
            lines.append("【资金主线·主力净流入TOP3】")
            lines.append(fund)
        # 板块异动（收盘口径方向）
        sec = _sector_moves_block(conn)
        if sec:
            lines.append("")
            lines.append("【板块异动（最近交易日涨幅TOP3）】")
            lines.append(sec)
        # 资金焦点·龙头
        cap = _capital_leaders_block(conn)
        if cap:
            lines.append("")
            lines.append("【资金焦点·龙虎榜龙头】")
            lines.append(cap)

        # 持仓警戒（完整版 + 非 public；public=True 时不展示私有持仓信息）
        card_alerts = _card_alerts(conn, live_prices=live) if (not brief and not public) else []
        # 温度与仓位指导
        lines.append("")
        lines.append("📊 温度: " + (
            f"{score:.0f}/100" + (f" | 宽度{width:.0%}" if width is not None else "") if score is not None else "暂无"
        ) + f" → {_temp_guide(score)}")
        lines.append(f"🎯 仓位: {_rating_guide(conn)}")
        if card_alerts:
            lines.append("")
            lines.append("⚠️ 持仓警戒:")
            lines += [f"  - {a}" for a in card_alerts]
        # 今日操作建议（2026-08-18 方案A，两版都有）
        lines.append("")
        lines.append(f"📌 今日操作: {_action_guide(conn, score)}")
    finally:
        conn.close()
    lines.append("")
    lines.append("（数据失效即防守：行情不新鲜时不作 P0 决策）")
    return "\n".join(lines)


# ---------- 盘前信息早报（2026-08-16：交易日 8:40，简明扼要） ----------

def morning_brief_report(db_path: str) -> str:
    """盘前信息早报：只给关键且市场关注度高的信息，简明扼要。

    内容（全部来自系统已采数据，无需外部接口）：
    - 隔夜市场：温度/情绪周期/市场风格（一句话）；
    - 资金焦点：龙虎榜净买入 TOP（隔夜资金动向）；
    - 板块主线：昨日涨幅榜 + 短线强度（哪个方向在动）；
    - 今日关注：候选池/异常波动/评级仓位（简短）；
    - 宏观速览：M1-M2/PMI/社融（一行）。
    设计原则：信息密度高、每节 1-3 行，能扫读。
    """
    conn = connect(db_path)
    try:
        lines = []
        lines.append("📰 A股盘前信息早报")
        lines.append(f"数据截至: {_freshness(conn)}")
        lines.append("")

        # 1) 隔夜市场一句话
        temp_row = conn.execute(
            "SELECT score, profit_effect FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        score = float(temp_row["score"]) if temp_row else None
        width = float(temp_row["profit_effect"]) if temp_row and temp_row["profit_effect"] is not None else None
        cycle_txt = ""
        try:
            import pandas as _pd

            from invest.quant.emotion_cycle import emotion_cycle
            emo = _pd.read_sql_query(
                "SELECT date, limit_up_count, max_lianban, zhaban_rate FROM market_emotion ORDER BY date", conn,
            )
            cyc = emotion_cycle(emo) if not emo.empty else {"stage": "数据不足"}
            cycle_txt = f" | 情绪:{cyc['stage']}"
        except Exception:
            pass
        style_txt = _style_block(conn).split("\n")[0] if _style_block(conn) else ""
        temp_txt = f"温度{score:.0f}/100" + (f"·宽度{width:.0%}" if width is not None else "") if score is not None else "温度暂无"
        lines.append(f"【隔夜市场】{temp_txt}{cycle_txt}" + (f" | {style_txt}" if style_txt else ""))
        # 隔夜外围（2026-08-21：美股/富时A50/商品/汇率快照）
        try:
            from invest.data.global_snapshot import global_snapshot_text

            global_txt = global_snapshot_text()
            if global_txt:
                lines.append(f"【隔夜外围】{global_txt}")
        except Exception:
            pass
        lines.append("")

        # 2) 资金焦点：龙虎榜净买入 TOP5（隔夜资金动向）
        try:
            rows = conn.execute(
                """SELECT symbol, name, net FROM dragon_tiger
                   WHERE date = (SELECT MAX(date) FROM dragon_tiger) AND net IS NOT NULL
                   ORDER BY net DESC LIMIT 5"""
            ).fetchall()
            if rows:
                lines.append("【资金焦点·龙虎榜净买入】")
                for r in rows:
                    name = r["name"] or r["symbol"]
                    lines.append(f"  {name} 净买{r['net']/1e8:+.2f}亿")
                lines.append("")
        except Exception:
            pass

        # 3) 板块主线：昨日涨幅 + 短线强度（取强度榜前3）
        lines.append("【板块主线】")
        try:
            top = conn.execute(
                """SELECT obj, rs, trend_stage FROM quant_strength
                   WHERE period='short' AND obj_type='industry'
                     AND run_date=(SELECT MAX(run_date) FROM quant_strength
                                   WHERE period='short' AND obj_type='industry')
                   ORDER BY rs DESC LIMIT 3"""
            ).fetchall()
            if top:
                lines.append("  强度: " + " | ".join(
                    f"{r['obj']}(rs{r['rs']:+.0%},{r['trend_stage']})" for r in top
                ))
        except Exception:
            pass
        try:
            mv = _movers_block(conn, n=3)
            if mv and "当日涨幅前3" in mv or mv and "当日涨幅前5" in mv:
                up_lines = [l for l in mv.split("\n") if "当日涨幅前" in l or (l and l[0] not in "当")]
                lines.append("  " + " | ".join(up_lines[1:4]) if len(up_lines) > 1 else "")
        except Exception:
            pass
        lines.append("")

        # 4) 今日关注：候选池 + 异常波动 + 评级仓位
        try:
            pool = conn.execute(
                "SELECT symbol, level, industry FROM candidate_pool WHERE out_date IS NULL ORDER BY level LIMIT 6"
            ).fetchall()
            if pool:
                lines.append("【今日关注·候选池】")
                lines.append("  " + " ".join(
                    f"{r['symbol']}({r['level']})" + (f"[{r['industry']}]" if r["industry"] else "") for r in pool
                ))
                lines.append("")
        except Exception:
            pass
        abnormal = _abnormal_moves(conn, n=3)
        if abnormal:
            lines.append("【今日关注·异常波动】")
            for a in abnormal:
                lines.append(f"  {a['symbol']} {a['signal']}")
            lines.append("")
        lines.append(f"🎯 仓位: {_rating_guide(conn)}")
        lines.append("")

        # 5) 宏观速览（一行）
        macro = _macro_text(conn)
        if macro:
            lines.append(f"【宏观】{macro}")
        msg = "\n".join(lines)
    finally:
        conn.close()
    return msg
