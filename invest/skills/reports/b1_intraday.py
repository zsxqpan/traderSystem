"""B1 盘中实时报告 skill（2026-08-22 v3：4 点结构化 + ETF + 简洁/完整版）。

结构（用户指定 v3）：
1. 盘面总览：主要指数实时 + 涨跌分化结构 + 指数 ETF 大资金信号（图表：指数涨跌幅条形图）；
2. 整体情绪判断 + 盘面预测 + 短线周期博弈判断（youzi 方法论，LLM）；
3. 日内主线：最强方向 → 原因/内部结构/**板块ETF强度**/**推荐关注股票**/最强股票/走势推演（LLM）；
4. 核心关注与预案对照（点4+5 合并）：核心关注行情表格 + 核心关注所在板块补充分析
   （若点3 未覆盖）+ 走势推演 + 盘后预案对照（读 viewpoints source='plan'）。

- 简洁版（brief=True，用户说"简洁/简短"时）：去所有推演/观点/预案，只留客观盘面
  （盘面总览 + 板块/ETF 客观数据 + 核心关注行情表格）；
- 完整版（默认）全量。
- struct 附 "views"（情绪/主线观点摘要），由发送层落库 viewpoints source='intraday_report'，
  供盘后日报点2 复盘（skill render 保持纯函数无副作用）。

LLM：job='intraday_report'，2 次调用（mood / mainline），_intraday_llm 缓存防抖。
"""
from __future__ import annotations

import datetime as dt

from invest.db import connect

SKILL = {
    "id": "b1_intraday",
    "name": "盘中实时报告",
    "kind": "report",
    "description": "盘中实时报告：盘面总览(含ETF)/情绪判断+预测/日内主线(ETF+推荐股)/核心关注与预案对照",
    "uses": ["d8_temp_guide", "d9_rating_guide", "d11_emotion", "d12_limit_up_ladder",
             "d13_fund_line", "d21_freshness", "d29_sector_resonance"],
    "params": {
        "db_path": "str, required",
        "public": "bool, optional, default False",
        "brief": "bool, optional, default False（2026-08-22：默认完整版，简洁版需显式）",
    },
}


# ---------- 数据组装 ----------

def _index_table() -> tuple[list[list[str]], str]:
    """盘面总览：指数实时表格行 + 结构分化文字。失败返回 ([], "")。"""
    try:
        from invest.data.index_realtime import fetch_index_realtime

        idx = fetch_index_realtime()
    except Exception:
        return [], ""
    if not idx:
        return [], ""
    order = ("000001", "399001", "000300", "000905", "000852", "000688", "399006", "899050")
    rows: list[list[str]] = []
    for code in order:
        d = idx.get(code)
        if d:
            rows.append([d["name"], f"{d['price']:.2f}", f"{d['pct']:+.2f}%"])
    small, large = idx.get("000852"), idx.get("000300")
    struct = ""
    if small and large and small["pct"] is not None and large["pct"] is not None:
        diff = small["pct"] - large["pct"]
        if diff >= 0.3:
            struct = (f"结构: **小盘强于大盘**（中证1000 {small['pct']:+.2f}% "
                      f"vs 沪深300 {large['pct']:+.2f}%，差 {diff:+.2f}%）")
        elif diff <= -0.3:
            struct = (f"结构: **大盘强于小盘**（沪深300 {large['pct']:+.2f}% "
                      f"vs 中证1000 {small['pct']:+.2f}%，差 {diff:+.2f}%）")
        else:
            struct = f"结构: 大小盘均衡（中证1000 {small['pct']:+.2f}% / 沪深300 {large['pct']:+.2f}%）"
    return rows, struct


def _index_etf_text() -> str:
    """指数 ETF 大资金信号（公共函数，invest/data/etf）。"""
    try:
        from invest.data.etf import index_etf_signal_text

        return index_etf_signal_text()
    except Exception:
        return ""


def _sector_etf_text() -> str:
    """全部重要板块 ETF 数据行（公共函数，invest/data/etf）。"""
    try:
        from invest.data.etf import sector_etf_text

        return sector_etf_text()
    except Exception:
        return ""


def _index_chart_data(rows: list[list[str]]) -> list[dict]:
    out = []
    for r in rows:
        try:
            out.append({"name": r[0], "value": float(r[2].replace("%", "").replace("+", ""))})
        except (ValueError, IndexError):
            continue
    return out


def _temp_hist(conn) -> list[float]:
    rows = conn.execute(
        "SELECT score FROM quant_temperature ORDER BY run_date DESC LIMIT 20"
    ).fetchall()
    return [float(r["score"]) for r in rows if r["score"] is not None][::-1]


def _emotion_text(conn) -> str:
    try:
        import pandas as pd

        from invest.quant.emotion_cycle import emotion_cycle

        emo = pd.read_sql_query(
            "SELECT date, limit_up_count, max_lianban, zhaban_rate FROM market_emotion ORDER BY date", conn,
        )
        cyc = emotion_cycle(emo) if not emo.empty else {"stage": "数据不足"}
        txt = f"情绪周期「{cyc['stage']}」"
        if cyc.get("reasons"):
            txt += "；" + "；".join(cyc["reasons"])
        return txt
    except Exception:
        return ""


def _intraday_limit_up(conn) -> str:
    try:
        rows = conn.execute(
            "SELECT symbol, lianban, zhaban FROM limit_up_pool "
            "WHERE date=(SELECT MAX(date) FROM limit_up_pool)"
        ).fetchall()
        if not rows:
            return ""
        zt = [r for r in rows if not r["zhaban"]]
        zhaban = [r for r in rows if r["zhaban"]]
        n_zt = len({r["symbol"] for r in zt})
        max_lb = max((int(r["lianban"] or 0) for r in zt), default=0)
        zr = len(zhaban) / len(rows) if rows else 0.0
        return f"涨停{n_zt}家 最高板{max_lb}板 炸板率{zr:.0%}"
    except Exception:
        return ""


def _sector_top(conn, n: int = 5) -> str:
    try:
        rows = conn.execute(
            """SELECT t.industry, t.close, p.close AS prev
               FROM industry_bars t
               JOIN industry_bars p ON p.industry = t.industry
                 AND p.date = (SELECT MAX(date) FROM industry_bars
                               WHERE industry=t.industry AND date < t.date)
               WHERE t.date = (SELECT MAX(date) FROM industry_bars)
                 AND t.close IS NOT NULL AND p.close IS NOT NULL AND p.close > 0
               ORDER BY (t.close/p.close - 1) DESC LIMIT ?""",
            (n,),
        ).fetchall()
        return "\n".join(f"  {r['industry']} {(r['close']/r['prev']-1):+.2%}" for r in rows) or ""
    except Exception:
        return ""


def _fund_top(conn, n: int = 5) -> str:
    try:
        rows = conn.execute(
            """SELECT industry, main_net FROM sector_fund_flow
               WHERE date=(SELECT MAX(date) FROM sector_fund_flow)
               ORDER BY main_net DESC LIMIT ?""",
            (n,),
        ).fetchall()
        return "\n".join(f"  {r['industry']} 主力净流入{float(r['main_net'])/1e8:+.2f}亿" for r in rows) or ""
    except Exception:
        return ""


def _ladder_text(conn, n: int = 8) -> str:
    try:
        rows = conn.execute(
            """SELECT symbol, name, lianban, zhaban FROM limit_up_pool
               WHERE date=(SELECT MAX(date) FROM limit_up_pool) AND zhaban=0
               ORDER BY lianban DESC, symbol LIMIT ?""",
            (n,),
        ).fetchall()
        return "\n".join(
            f"  {r['symbol']} {r['name'] or ''} {int(r['lianban'] or 0)}板" for r in rows
        ) or ""
    except Exception:
        return ""


def _core_quotes(db_path: str) -> tuple[list[list[str]], str]:
    conn = connect(db_path)
    try:
        core = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level IN ('core','track') "
            "AND out_date IS NULL ORDER BY level"
        )]
        # 2026-08-26：实时失败回退收盘价（表格不空白；盘中报告核心关注必须可见）
        fallback = {r["symbol"]: float(r["close"]) for r in conn.execute(
            """SELECT d.symbol, d.close FROM daily_bars d
               JOIN (SELECT symbol, MAX(REPLACE(date,'-','')) md FROM daily_bars
                     WHERE symbol IN (%s) GROUP BY symbol) m
               ON d.symbol=m.symbol AND REPLACE(d.date,'-','')=m.md""" % ",".join("?" * len(core)),
            core,
        )} if core else {}
    finally:
        conn.close()
    if not core:
        return [], ""
    try:
        from invest.report import _live_quotes

        live, pct_map = _live_quotes(db_path, core)
    except Exception:
        live, pct_map = {}, {}
    rows, lines = [], []
    for sym in core:
        price = live.get(sym)
        pct = pct_map.get(sym)
        if price is None and sym in fallback:
            # 实时不可用 → 收盘价回退（标注来源，防止误当实时）
            price, pct = fallback[sym], None
        if price is None:
            continue
        pct_txt = f"{pct:+.2%}" if pct is not None else "—(收盘)"
        rows.append([sym, f"{price:.2f}", pct_txt])
        lines.append(f"{sym} {price:.2f} ({pct_txt})")
    return rows, "；".join(lines)


def _core_industry(conn, symbols: list[str]) -> str:
    """核心关注所属行业（industry_map），供点4 补板块分析。"""
    try:
        from invest.data.industry_map import industry_of

        inds = {}
        for s in symbols:
            ind = industry_of(conn, s)
            if ind:
                inds[ind] = inds.get(ind, 0) + 1
        return "、".join(f"{k}" for k in sorted(inds, key=lambda x: -inds[x])[:3]) or ""
    except Exception:
        return ""


def _read_plan(conn) -> str:
    """盘后预案对照：读 viewpoints source='plan' 最近 active，把 JSON 结论格式化为可读文本。"""
    try:
        import json

        rows = conn.execute(
            """SELECT conclusion FROM viewpoints WHERE source='plan' AND status='active'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchall()
        lines: list[str] = []
        for r in rows:
            try:
                plan = json.loads(r["conclusion"])
            except (ValueError, TypeError):
                lines.append(f"  - {str(r['conclusion'])[:200]}")
                continue
            if not isinstance(plan, dict):
                lines.append(f"  - {str(plan)[:200]}")
                continue
            if plan.get("direction"):
                lines.append(f"  方向: {plan['direction']}")
            for p in (plan.get("picks") or []):
                name = p.get("name", "")
                sym = f"({p.get('symbol', '')})" if p.get("symbol") else ""
                lines.append(f"  · 介入 {name}{sym}：{p.get('reason', '')}｜{p.get('plan', '')}")
            for p in (plan.get("plans") or []):
                lines.append(f"  · {p.get('symbol', '')}：{p.get('action', '')}")
            if not lines:
                lines.append("  - （预案内容为空）")
        return "\n".join(lines)
    except Exception:
        return ""


# ---------- 组装 ----------

def render(db_path: str, public: bool = False, brief: bool = False) -> dict:
    from invest.skills.sections import _intraday_llm

    sections: list[dict] = []
    views: dict = {}
    now = dt.datetime.now()

    # ---- 1) 盘面总览（指数表格 + 结构 + 指数ETF大资金信号） ----
    # 2026-08-24：去掉 index_bars 图表——表格已含全部指数涨跌幅，重复展示
    idx_rows, struct = _index_table()
    if idx_rows:
        sections.append({
            "type": "table", "title": "盘面总览",
            "columns": ["指数", "点位", "涨跌幅"], "rows": idx_rows,
        })
        if struct:
            sections.append({"type": "text", "text": struct})
        etf_sig = _index_etf_text()
        if etf_sig:
            sections.append({
                "type": "text",
                "text": "**【指数ETF·大资金信号】**\n" + etf_sig +
                        "\n（量比明显放大/超大单大额进出≈国家队或大资金动作）",
            })
    else:
        sections.append({"type": "text", "text": "（指数实时暂不可用）"})

    # ---- 2) 情绪判断 + 盘面预测（LLM，失败回退规则） ----
    conn = connect(db_path)
    try:
        temp_row = conn.execute(
            "SELECT score FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        score = float(temp_row["score"]) if temp_row and temp_row["score"] is not None else None
        temp_text = f"{score:.0f}/100" if score is not None else "暂无"
        emo_text = _emotion_text(conn)
        lu_text = _intraday_limit_up(conn)
        temp_hist = _temp_hist(conn)
    finally:
        conn.close()

    mood = _intraday_llm.mood_llm(db_path, {
        "temp_text": temp_text, "emotion_text": emo_text,
        "limit_up_text": lu_text, "temp_hist": temp_hist,
    })
    if mood.get("mood"):
        lines = [f"**情绪判断**: {mood.get('mood', '')}"]
        if mood.get("prediction"):
            lines.append(f"**盘面预测**: {mood['prediction']}")
        if mood.get("short_term"):
            lines.append(f"**短线博弈**: {mood['short_term']}")
        sections.append({"type": "text", "text": "**【情绪与预测】**\n" + "\n".join(lines)})
        views["mood"] = mood
    elif not brief:
        parts = [f"温度 {temp_text}"]
        if emo_text:
            parts.append(emo_text)
        if lu_text:
            parts.append(f"盘中 {lu_text}")
        sections.append({"type": "text", "text": "**【情绪与预测】**\n" + "；".join(parts)})

    # ---- 3) 日内主线（LLM：原因/内部/ETF/推荐股/龙头/推演；失败回退直列） ----
    conn = connect(db_path)
    try:
        sector_top = _sector_top(conn)
        fund_top = _fund_top(conn)
        ladder = _ladder_text(conn)
    finally:
        conn.close()
    _core_rows, core_lines = _core_quotes(db_path)
    etf_sector = _sector_etf_text()
    mainline = _intraday_llm.mainline_llm(db_path, {
        "sector_top": sector_top, "fund_top": fund_top,
        "ladder": ladder, "core": core_lines, "etf_sector": etf_sector,
    })
    lines: list[str] = []
    for ml in (mainline.get("main_lines") or []):
        lines.append(f"**{ml.get('direction', '')}**")
        if ml.get("reason"):
            lines.append(f"  · 原因: {ml['reason']}")
        if ml.get("internal"):
            lines.append(f"  · 内部: {ml['internal']}")
        if ml.get("etf"):
            lines.append(f"  · ETF: {ml['etf']}")
        for pk in (ml.get("picks") or []):
            lines.append(f"  · 关注: {pk.get('name', '')}（{pk.get('reason', '')}）")
        for ld in (ml.get("leaders") or []):
            lines.append(f"  · {ld.get('role', '')} {ld.get('name', '')}：{ld.get('analysis', '')}")
        if ml.get("outlook"):
            lines.append(f"  → 推演: {ml['outlook']}")
    if lines:
        sections.append({"type": "text", "text": "**【日内主线】**\n" + "\n".join(lines)})
        views["mainline"] = mainline.get("main_lines")
    elif not brief and (sector_top or fund_top):
        blocks = []
        if fund_top:
            blocks.append("资金主线(实时):\n" + fund_top)
        if sector_top:
            blocks.append("板块涨幅(收盘参考):\n" + sector_top)
        if ladder:
            blocks.append("连板梯队:\n" + ladder)
        if etf_sector:
            blocks.append("板块ETF:\n" + etf_sector)
        sections.append({"type": "text", "text": "**【日内主线】**\n" + "\n\n".join(blocks)})
    elif not brief and etf_sector:
        sections.append({"type": "text", "text": "**【板块ETF强度】**\n" + etf_sector})

    # ---- 4) 核心关注与预案对照（点4+5 合并） ----
    conn = connect(db_path)
    try:
        core_inds = _core_industry(conn, [r[0] for r in _core_rows]) if _core_rows else ""
        plan = _read_plan(conn)
    finally:
        conn.close()
    if _core_rows:
        sections.append({
            "type": "table", "title": "核心关注实时行情",
            "columns": ["标的", "现价", "涨跌幅"], "rows": _core_rows,
        })
        # 核心关注所在板块：若点3 未覆盖则补一句客观数据
        if core_inds and brief:
            sections.append({
                "type": "text",
                "text": f"核心关注所属板块: {core_inds}（客观数据见上，推演见完整版）",
            })
        if not brief:
            if core_inds:
                sections.append({"type": "text", "text": f"**核心关注板块**: {core_inds}"})
            if mainline.get("core_outlook"):
                sections.append({"type": "text", "text": f"**走势推演**: {mainline['core_outlook']}"})
    if plan and not brief:
        sections.append({"type": "text", "text": "**【与盘后预案对照】**\n" + plan})

    # 2026-08-23 角度 skill 复用：板块共振（d29），失败静默不阻断
    if not brief:
        try:
            from invest.skills.sections.d29_sector_resonance import render as _resonance_render

            resonance = _resonance_render(db_path)
            if resonance:
                sections.append({"type": "text", "text": resonance})
        except Exception:
            pass

    if not brief:
        sections.append({
            "type": "text",
            "text": "（数据失效即防守：行情不新鲜时不作 P0 决策）· " + now.strftime("%H:%M"),
        })
    return {"title": f"A股投资系统 · 盘中报告 {now.strftime('%H:%M')}",
            "sections": sections, "views": views}
