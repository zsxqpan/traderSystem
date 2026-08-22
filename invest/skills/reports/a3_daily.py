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
    "uses": ["d1_news_block", "d16_card_alerts", "d17_pool_delta", "d21_freshness",
             "d12_limit_up_ladder", "d13_fund_line"],
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


def _today_views_text(conn) -> str:
    """今日盘中报告观点（source='intraday_report'）。"""
    try:
        rows = conn.execute(
            """SELECT obj, conclusion FROM viewpoints
               WHERE source='intraday_report' AND date(created_at)=date('now','localtime')
               ORDER BY created_at"""
        ).fetchall()
        lines = []
        for r in rows:
            try:
                d = json.loads(r["conclusion"])
                lines.append(f"- [{r['obj']}] {json.dumps(d, ensure_ascii=False)[:300]}")
            except ValueError:
                lines.append(f"- [{r['obj']}] {r['conclusion'][:200]}")
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
    """关注/持仓股（cards + 候选池 core）最新收盘。"""
    conn = connect(db_path)
    try:
        symbols = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM cards WHERE status IN ('locked','review')"
        ).fetchall()]
        symbols += [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level='core' AND out_date IS NULL"
        ).fetchall()]
        symbols = list(dict.fromkeys(symbols))
        lines = []
        for s in symbols:
            row = conn.execute(
                "SELECT close FROM daily_bars WHERE symbol=? "
                "ORDER BY REPLACE(date,'-','') DESC LIMIT 1", (s,),
            ).fetchone()
            price = f"{row['close']:.2f}" if row and row["close"] is not None else "?"
            lines.append(f"{s}({price})")
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
        # 次日实际：推荐股涨跌
        actual = []
        for p in picks:
            sym = (p.get("symbol") or "").strip()
            if not sym or nxt is None:
                continue
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
        "text": f"**【A股投资系统 · 盘后日报】**\n数据截至: {freshness}",
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

    # ---- 点2 盘中观点复盘（LLM） ----
    conn = connect(db_path)
    try:
        views_text = _today_views_text(conn)
        actual_text = _today_actual_text(conn)
    finally:
        conn.close()
    review = _daily_llm.intraday_review_llm(db_path, {
        "views_text": views_text, "actual_text": actual_text,
    })
    if review.get("verdict"):
        lines = [f"**对错总结**: {review['verdict']}"]
        for r in (review.get("wrong_reasons") or []):
            lines.append(f"  · 错误原因: {r}")
        for l in (review.get("lessons") or []):
            lines.append(f"  · 经验: {l}")
        sections.append({"type": "text", "text": "**【点2 盘中观点复盘】**\n" + "\n".join(lines)})
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
        "ladder": ladder, "stock_moves": moves,
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

    # ---- 点4 明日预案（推荐 + 操作预案 + 质量复盘） ----
    summary = "\n".join(
        s.get("text", "") for s in sections if s.get("type") == "text"
    )[:1500]
    holdings = _holdings_text(db_path)
    conn = connect(db_path)
    try:
        history = _plan_history(conn)
    finally:
        conn.close()
    plan = _daily_llm.plan_gen_llm(db_path, {
        "summary": summary, "holdings": holdings,
        "plan_history": json.dumps(history, ensure_ascii=False)[:800],
    })
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

    return {"title": "A股投资系统 · 盘后日报", "sections": sections, "plan_data": plan_data}
