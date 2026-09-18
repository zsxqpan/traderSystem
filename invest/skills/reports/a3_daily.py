"""A3 盘后日报 skill（2026-08-22 重构：盘中报告 PLUS 版，4 点结构化 + 预案闭环）。

结构（用户指定）：
1. 盘面总览（收盘角度）：指数表格 + **指数 ETF 分析**（量能/资金流入流出/大资金进出≈国家队）；
2. **盘中观点复盘**：今日盘中报告给过的预测/操作建议/短线判断 vs 当日实际 → 对错总结 +
   错误原因（沉淀经验，可固化 skill）；
3. **重要板块总分析**：AI硬件/AI软件/机器人/金融/金属/新旧能源/内需 固定方向 + 各方向 ETF
   （纯度高于板块指数）——活跃方向详细分析；不活跃一句话中线状态；方向内个股异动一句话归因；
4. **明日预案**：推荐介入股票（系统探索迭代）+ 关注/持仓股操作预案 + 最近 N 日预案质量复盘
   （优化预案推演方式，可沉淀为 skill）。

尾部保留（Q1-A）：持仓警戒 / 消息面(LLM) / 候选池变化。
struct 附 "plan_data"（明日预案 JSON），由调度器写 viewpoints source='plan'（B1 预案对照
与次日复盘读取；skill render 保持纯函数）。

LLM：job='daily_report'，4 次调用（intraday_review / board_analysis / plan_gen / plan_review），
失败回退（省略该节/直列数据），不阻断报告。
"""
from __future__ import annotations

import datetime as dt
import json

from invest.db import connect

SKILL = {
    "id": "a3_daily",
    "name": "盘后日报",
    "kind": "report",
    "description": "盘后日报（盘中PLUS）：盘面总览(含ETF)/盘中观点复盘/重要板块总分析/明日预案+质量复盘",
    "uses": ["d1_news_block", "d10_action_guide", "d16_card_alerts", "d17_pool_delta",
             "d21_freshness", "d12_limit_up_ladder", "d13_fund_line", "d28_community_hot",
             "d29_sector_resonance", "d32_trade_signals", "d33_daily_actions"],
    "params": {
        "db_path": "str, required",
        "agent_text": "str, optional, default ''（兼容旧调用，新结构未使用）",
    },
}


# ---------- 数据组装 ----------

def _index_table() -> list[list[str]]:
    try:
        from invest.data.index_realtime import fetch_index_realtime

        idx = fetch_index_realtime()
    except Exception:
        return []
    order = ("000001", "399001", "000300", "000905", "000852", "000688", "399006", "899050")
    rows = []
    for code in order:
        d = idx.get(code)
        if d:
            rows.append([d["name"], f"{d['price']:.2f}", f"{d['pct']:+.2f}%"])
    return rows


def _index_etf_rows() -> list[list[str]]:
    """指数 ETF 表格行（涨跌幅/成交额/换手/量比/主力/超大单）——点1 ETF 分析。"""
    try:
        from invest.data.etf import INDEX_ETFS, fetch_etf_quotes

        quotes = fetch_etf_quotes(list(INDEX_ETFS))
    except Exception:
        return []
    rows = []
    for code in ("510300", "510050", "510500", "512100", "159915", "588000"):
        q = quotes.get(code)
        if not q:
            continue
        rows.append([
            q.get("name") or code,
            f"{q['pct']:+.2f}%" if q.get("pct") is not None else "-",
            f"{q['amount']/1e8:.1f}亿" if q.get("amount") else "-",
            f"{q['turnover']:.2f}%" if q.get("turnover") is not None else "-",
            f"{q['vol_ratio']:.2f}" if q.get("vol_ratio") is not None else "-",
            f"{q['main_net']/1e8:+.2f}亿" if q.get("main_net") else "-",
            f"{q['super_net']/1e8:+.2f}亿" if q.get("super_net") else "-",
        ])
    return rows


def _etf_analysis_text(db_path: str, quotes: dict) -> str:
    """指数 ETF 解读（2026-08-24）：无异常用规则简单解读（0 token）；
    明显变化（涨跌≥1.2%/量比≥1.8/超大单占比≥0.5%）→ LLM 详细归因 + 风格变化探讨。"""
    try:
        rows = []
        for code in ("510300", "510050", "510500", "512100", "159915", "588000"):
            q = quotes.get(code)
            if not q:
                continue
            pct = q.get("pct")
            vol = q.get("vol_ratio")
            amount = q.get("amount") or 0
            super_net = q.get("super_net") or 0
            rows.append({"name": q.get("name") or code, "pct": pct, "vol_ratio": vol,
                         "super_net": super_net, "amount": amount})
        if not rows:
            return ""
        notable = [r for r in rows
                   if (r["pct"] is not None and abs(r["pct"]) >= 1.2)
                   or (r["vol_ratio"] is not None and r["vol_ratio"] >= 1.8)
                   or (r["amount"] and abs(r["super_net"]) / r["amount"] >= 0.005)]
        if not notable:
            parts = [f"{r['name']} {r['pct']:+.2f}%" for r in rows if r["pct"] is not None]
            if not parts:
                return ""
            return "ETF 整体平稳（" + "；".join(parts[:4]) + "），未见明显放量或资金异动。"
        from invest.skills.sections import _daily_llm

        material = "\n".join(
            f"{r['name']} 涨跌{r['pct']:+.2f}% 量比{r['vol_ratio'] or '-'} "
            f"主力净{r['super_net'] / 1e8:+.2f}亿 成交{r['amount'] / 1e8:.0f}亿" for r in rows)
        out = _daily_llm.etf_analysis_llm(db_path, {"material": material})
        lines = []
        if out.get("summary"):
            lines.append(f"**整体**: {out['summary']}")
        if out.get("notable"):
            lines.append(f"**异动**: {out['notable']}")
        if out.get("attribution"):
            lines.append(f"**归因**: {out['attribution']}")
        if out.get("style_shift"):
            lines.append(f"**风格变化可能**: {out['style_shift']}")
        return "\n".join(lines)
    except Exception:
        return ""


def _today_views_text(conn) -> str:
    """今日观点（2026-08-22：盘中报告 intraday_report + 竞价报告 auction_report）。"""
    try:
        rows = conn.execute(
            """SELECT source, obj, conclusion FROM viewpoints
               WHERE source IN ('intraday_report','auction_report')
                 AND date(created_at)=date('now','localtime')
               ORDER BY created_at"""
        ).fetchall()
        lines = []
        for r in rows:
            tag = {"intraday_report": "盘中", "auction_report": "竞价"}.get(r["source"], r["source"])
            try:
                d = json.loads(r["conclusion"])
                lines.append(f"- [{tag}·{r['obj']}] {json.dumps(d, ensure_ascii=False)[:300]}")
            except ValueError:
                lines.append(f"- [{tag}·{r['obj']}] {r['conclusion'][:200]}")
        return "\n".join(lines)
    except Exception:
        return ""


def _today_actual_text(conn) -> str:
    """当日实际：指数涨跌 + 板块涨幅 TOP + 连板。"""
    parts = []
    idx = _index_table()
    if idx:
        parts.append("指数: " + " ".join(f"{r[0]} {r[2]}" for r in idx))
    try:
        rows = conn.execute(
            """SELECT t.industry, t.close, p.close AS prev
               FROM industry_bars t
               JOIN industry_bars p ON p.industry = t.industry
                 AND p.date = (SELECT MAX(date) FROM industry_bars
                               WHERE industry=t.industry AND date < t.date)
               WHERE t.date = (SELECT MAX(date) FROM industry_bars)
                 AND t.close IS NOT NULL AND p.close IS NOT NULL AND p.close > 0
               ORDER BY (t.close/p.close - 1) DESC LIMIT 5"""
        ).fetchall()
        if rows:
            parts.append("板块涨幅TOP: " + " ".join(f"{r['industry']} {(r['close']/r['prev']-1):+.2%}" for r in rows))
    except Exception:
        pass
    return "\n".join(parts)


def _holdings_text(db_path: str) -> str:
    """关注/持仓股：代码 + 收盘 + cards 价位字段。"""
    conn = connect(db_path)
    try:
        cards = {r["symbol"]: dict(r) for r in conn.execute(
            "SELECT symbol, entry_range, stop_loss, target, falsify FROM cards "
            "WHERE status IN ('locked','review')"
        )}
        symbols = list(cards)
        symbols += [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level='core' AND out_date IS NULL"
        )]
        symbols = list(dict.fromkeys(symbols))
        lines = []
        for s in symbols:
            row = conn.execute(
                "SELECT close FROM daily_bars WHERE symbol=? "
                "ORDER BY REPLACE(date,'-','') DESC LIMIT 1", (s,),
            ).fetchone()
            price = f"{row['close']:.2f}" if row and row["close"] is not None else "?"
            card = cards.get(s) or {}
            bits = [price]
            if card.get("entry_range"):
                bits.append(f"入{card['entry_range']}")
            if card.get("stop_loss") is not None:
                bits.append(f"止{card['stop_loss']}")
            if card.get("target") is not None:
                bits.append(f"标{card['target']}")
            if card.get("falsify"):
                bits.append(f"证伪:{card['falsify']}")
            lines.append(f"{s}({' '.join(bits)})")
        return " ".join(lines) if lines else ""
    finally:
        conn.close()


def _next_trading_day(d: dt.date) -> dt.date | None:
    """d 之后第一个交易日（最近 7 天内查找）。"""
    from invest.data.calendar import is_trading_day

    for i in range(1, 8):
        cand = d + dt.timedelta(days=i)
        if is_trading_day(cand):
            return cand
    return None


def _plan_history(conn) -> list[dict]:
    """最近 5 天预案 vs 次日实际（预案质量复盘输入）。"""
    try:
        rows = conn.execute(
            """SELECT created_at, conclusion FROM viewpoints
               WHERE source='plan' ORDER BY created_at DESC LIMIT 5"""
        ).fetchall()
    except Exception:
        return []
    history = []
    for r in reversed(rows):
        try:
            plan = json.loads(r["conclusion"])
        except (ValueError, TypeError):
            continue
        date_str = (r["created_at"] or "")[:10]
        try:
            d = dt.date.fromisoformat(date_str)
        except ValueError:
            continue
        nxt = _next_trading_day(d)
        picks = plan.get("picks") or []
        picks_txt = "、".join(
            f"{p.get('name', '')}" + (f"({p.get('symbol', '')})" if p.get("symbol") else "")
            for p in picks
        ) or "无推荐"
        # 次日实际：picks + plans 标的涨跌
        actual = []
        seen_sym: set[str] = set()
        for p in list(picks) + list(plan.get("plans") or []):
            sym = (p.get("symbol") or "").strip()
            if not sym or nxt is None or sym in seen_sym:
                continue
            seen_sym.add(sym)
            row = conn.execute(
                "SELECT close FROM daily_bars WHERE symbol=? AND date=?", (sym, nxt.isoformat()),
            ).fetchone()
            prev = conn.execute(
                "SELECT close FROM daily_bars WHERE symbol=? AND date=?", (sym, d.isoformat()),
            ).fetchone()
            if row and prev and prev["close"]:
                actual.append(f"{sym} {(row['close']/prev['close']-1):+.2%}")
        plan_summary = f"方向:{plan.get('direction', '')} 推荐:{picks_txt}"
        actual_summary = (" 实际:" + " ".join(actual)) if actual else " 实际:（数据不足）"
        history.append({"date": date_str, "plan_summary": plan_summary,
                        "actual_summary": actual_summary})
    return history


# ---------- 组装 ----------

def render(db_path: str, agent_text: str = "") -> dict:
    from invest.skills.sections import _daily_llm

    sections: list[dict] = []
    plan_data: dict = {}

    # ---- 标题 + 数据截至 ----
    conn = connect(db_path)
    try:
        from invest.report import _freshness

        freshness = _freshness(conn)
    finally:
        conn.close()
    sections.append({
        "type": "text",
        "text": f"**【A股投资系统 · 盘后日报】**\n\n数据截至: {freshness}",
    })

    # ---- 点1 盘面总览（指数 + ETF 分析） ----
    idx_rows = _index_table()
    if idx_rows:
        sections.append({
            "type": "table", "title": "点1 盘面总览·指数",
            "columns": ["指数", "点位", "涨跌幅"], "rows": idx_rows,
        })
    etf_rows = _index_etf_rows()
    if etf_rows:
        sections.append({
            "type": "table", "title": "点1 指数ETF（量能/资金/大资金进出）",
            "columns": ["ETF", "涨跌幅", "成交额", "换手", "量比", "主力净流入", "超大单"],
            "rows": etf_rows,
        })
        try:
            from invest.data.etf import index_etf_signal_text

            sig = index_etf_signal_text()
            if sig:
                sections.append({
                    "type": "text",
                    "text": "**大资金信号**（量比放大/超大单大额进出≈国家队或大资金动作）:\n" + sig,
                })
        except Exception:
            pass
        # 2026-08-24：指数 ETF 解读——简单默认，明显变化 LLM 详细归因+风格探讨（失败静默）
        try:
            from invest.data.etf import INDEX_ETFS, fetch_etf_quotes

            _etf_quotes = fetch_etf_quotes(list(INDEX_ETFS))
            etf_analysis = _etf_analysis_text(db_path, _etf_quotes)
            if etf_analysis:
                sections.append({
                    "type": "text",
                    "text": "**【指数ETF解读】**\n" + etf_analysis,
                })
        except Exception:
            pass

    sigs: list = []
    sig_text = ""
    try:
        from invest.signals.format import (
            format_mid_quadrants,
            format_signals,
            rows_to_signals,
            signal_section,
        )
        from invest.signals.query import list_signals
        from invest.signals.scan import scan_db
        from invest.signals.thresholds import DISPLAY_A3

        sigs = scan_db(db_path, "close", persist=True, limit=DISPLAY_A3)
        short_text = format_signals(sigs, limit=DISPLAY_A3, db_path=db_path)
        blk = signal_section(sigs, limit=DISPLAY_A3, db_path=db_path)
        if blk:
            sections.append(blk)
        conn_m = connect(db_path)
        try:
            mid_rows = list_signals(conn_m, horizon="mid", session="daily", limit=200)
        finally:
            conn_m.close()
        mid_sigs = rows_to_signals(mid_rows)
        quad = format_mid_quadrants(mid_sigs)
        if quad:
            title, _, rest = quad.partition("\n")
            sections.append({
                "type": "text",
                "text": f"**{title}**\n{rest}" if rest else f"**{title}**",
            })
        sig_text = "\n".join(x for x in (short_text, quad) if x)
    except Exception:
        sigs, sig_text = [], ""

    # ---- 点2 盘中观点复盘（LLM） ----
    conn = connect(db_path)
    try:
        views_text = _today_views_text(conn)
        actual_text = _today_actual_text(conn)
    finally:
        conn.close()
    review = _daily_llm.intraday_review_llm(db_path, {
        "views_text": views_text, "actual_text": actual_text, "signals_text": sig_text,
    })
    if review.get("verdict"):
        lines = [f"**对错总结**: {review['verdict']}"]
        for r in (review.get("wrong_reasons") or []):
            lines.append(f"  · 错误原因: {r}")
        for l in (review.get("lessons") or []):
            lines.append(f"  · 经验: {l}")
        sections.append({"type": "text", "text": "**【点2 盘中观点复盘】**\n" + "\n".join(lines)})
        try:
            from invest.actions.persist import persist_lessons

            conn_r = connect(db_path)
            try:
                persist_lessons(conn_r, dt.date.today(), "intraday",
                                list(review.get("lessons") or []))
            finally:
                conn_r.close()
        except Exception:
            pass
    elif views_text:
        sections.append({
            "type": "text",
            "text": "**【点2 盘中观点复盘】**（LLM 复盘失败，直列观点）\n" + views_text[:400],
        })

    # ---- 点3 重要板块总分析（LLM + ETF） ----
    conn = connect(db_path)
    try:
        from invest.report import _abnormal_moves

        sector_top = _index_table() and _today_actual_text(conn).split("板块涨幅TOP:")[-1].strip()
        ladder = "\n".join(
            f"  {r['symbol']} {r['name'] or ''} {int(r['lianban'] or 0)}板"
            for r in conn.execute(
                "SELECT symbol, name, lianban FROM limit_up_pool "
                "WHERE date=(SELECT MAX(date) FROM limit_up_pool) AND zhaban=0 "
                "ORDER BY lianban DESC, symbol LIMIT 8"
            ).fetchall()
        )
        moves = "\n".join(
            f"  {a['symbol']} {a['signal']}" for a in _abnormal_moves(conn, n=6)
        )
    finally:
        conn.close()
    try:
        from invest.data.etf import sector_etf_text

        etf_sector = sector_etf_text()
    except Exception:
        etf_sector = ""
    boards = _daily_llm.board_analysis_llm(db_path, {
        "etf_sector": etf_sector, "sector_top": sector_top,
        "ladder": ladder, "stock_moves": moves, "signals_text": sig_text,
    })
    blines = []
    for b in (boards.get("boards") or []):
        blines.append(f"**{b.get('name', '')}**" + ("（活跃）" if b.get("active") else "（平淡）"))
        if b.get("analysis"):
            blines.append(f"  {b['analysis']}")
        if b.get("stock_move"):
            blines.append(f"  · 个股异动: {b['stock_move']}")
    if blines:
        sections.append({"type": "text", "text": "**【点3 重要板块总分析】**\n" + "\n".join(blines)})
    elif etf_sector:
        sections.append({"type": "text", "text": "**【点3 板块ETF数据】**\n" + etf_sector})

    # ---- 点4 明日动作表 + 预案专栏 + 质量复盘 ----
    summary = "\n".join(
        s.get("text", "") for s in sections if s.get("type") == "text"
    )[:1500]
    holdings = _holdings_text(db_path)
    asof = dt.date.today()
    for_date = _next_trading_day(asof) or (asof + dt.timedelta(days=1))
    conn = connect(db_path)
    try:
        history = _plan_history(conn)
        from invest.actions.format import format_actions
        from invest.actions.persist import list_lessons
        from invest.report import _action_guide

        draft_text = ""
        lessons_txt = ""
        try:
            from invest.actions.compose import compose

            draft = compose(conn, asof, for_date=for_date)
            draft_text = format_actions(draft)
        except Exception:
            draft = []
        lessons_txt = "；".join(x["body"] for x in list_lessons(conn, asof=asof))
        score_row = conn.execute(
            "SELECT score FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        score = float(score_row["score"]) if score_row and score_row["score"] is not None else None
        guide = _action_guide(conn, score)
        if guide:
            sections.append({"type": "text", "text": f"📌 今日操作: {guide}"})
    finally:
        conn.close()
    plan = _daily_llm.plan_gen_llm(db_path, {
        "summary": summary, "holdings": holdings,
        "plan_history": json.dumps(history, ensure_ascii=False)[:800],
        "signals_text": sig_text,
        "actions_text": draft_text,
        "lessons_text": lessons_txt,
    })
    merged = []
    try:
        from invest.actions.compose import compose
        from invest.actions.format import action_table
        from invest.actions.persist import persist_actions

        conn_a = connect(db_path)
        try:
            merged = compose(conn_a, asof, for_date=for_date, llm_plan=plan)
            persist_actions(conn_a, merged, for_date)
            try:
                from invest.actions.watch import expire_due, maybe_open_from_actions

                expire_due(conn_a, asof)
                maybe_open_from_actions(conn_a, for_date)
            except Exception:
                pass
        finally:
            conn_a.close()
        tbl = action_table(merged, title="明日动作（规则）")
        if tbl:
            sections.append(tbl)
            sections.append({
                "type": "text",
                "text": "警戒=价位触达告警；动作表=明日清单",
            })
    except Exception:
        merged = []
    if plan.get("direction") or plan.get("picks") or plan.get("plans"):
        plines = [f"**明日主线**: {plan.get('direction', '')}"]
        for p in (plan.get("picks") or []):
            plines.append(f"  · 介入 {p.get('name', '')}：{p.get('reason', '')}｜{p.get('plan', '')}")
        for p in (plan.get("plans") or []):
            plines.append(f"  · {p.get('symbol', '')}：{p.get('action', '')}")
        sections.append({"type": "text", "text": "**【点4 明日预案】**\n" + "\n".join(plines)})
        plan_data = plan
    # 预案质量复盘
    if history:
        pv = _daily_llm.plan_review_llm(db_path, {"history": history})
        if pv.get("quality"):
            qlines = [f"**预案质量**: {pv['quality']}"]
            for f in (pv.get("fixes") or []):
                qlines.append(f"  · 改进: {f}")
            sections.append({
                "type": "text",
                "text": "**【预案质量复盘（近 N 日）】**\n" + "\n".join(qlines),
            })
        else:
            hlines = [f"[{h['date']}] {h['plan_summary']} {h['actual_summary']}" for h in history[-3:]]
            sections.append({
                "type": "text",
                "text": "**【预案质量复盘（近 N 日）】**（LLM 失败，直列）\n" + "\n".join(hlines),
            })
        try:
            from invest.actions.persist import persist_lessons

            conn_l = connect(db_path)
            try:
                persist_lessons(conn_l, asof, "plan", list(pv.get("fixes") or []))
            finally:
                conn_l.close()
        except Exception:
            pass

    # ---- 尾部保留：持仓警戒 / 消息面 / 候选池变化 ----
    conn = connect(db_path)
    try:
        from invest.report import _card_alerts, _pool_delta

        alerts = _card_alerts(conn)
        pool = _pool_delta(conn)
    finally:
        conn.close()
    if alerts:
        sections.append({
            "type": "text",
            "text": "**⚠️ 持仓警戒**\n" + "\n".join(f"  - {a}" for a in alerts),
        })
    try:
        from invest.report import _news_block

        news = _news_block(db_path, n=4, days=2, job="daily_report")
        if news:
            sections.append({"type": "text", "text": "**【消息面 · 大模型提炼（近2日）】**\n" + news})
    except Exception:
        pass
    if pool and pool != "无":
        sections.append({"type": "text", "text": "**【候选池变化】**\n" + pool})

    # 2026-08-23 角度 skill 复用：板块共振（d29）+ 社区热议（d28），失败静默不阻断
    try:
        from invest.skills.sections.d28_community_hot import render as _community_render
        from invest.skills.sections.d29_sector_resonance import render as _resonance_render

        resonance = _resonance_render(db_path)
        if resonance:
            sections.append({"type": "text", "text": resonance})
        community = _community_render(db_path, n=3, job="daily_report")
        if community:
            sections.append({"type": "text", "text": "**【社区热议 · 雪球/股吧】**\n" + community})
    except Exception:
        pass

    return {"title": "A股投资系统 · 盘后日报", "sections": sections, "plan_data": plan_data}
