"""B1 盘中实时报告 skill（2026-08-22 重构：5 节结构化输出，飞书卡片图表）。

新结构（用户指定）：
1. 盘面总览：主要指数实时 + 涨跌分化结构（图表：指数涨跌幅条形图）；
2. 整体情绪判断 + 盘面预测（结合多日数据）+ 短线周期博弈判断（youzi 方法论，LLM）；
3. 日内主线：最强方向（涨幅/资金）→ 原因/内部结构/最强股票(连板/趋势/容量/行业龙头)/
   走势推演（LLM，失败回退直列）；
4. 与盘后预案对照（预留：读 viewpoints source='plan'，预案模块建成后自动生效，无则省略）；
5. 核心关注实时行情（表格）+ 结合判断的走势推演。

render 返回 {"title", "sections"}（text/table/chart 节），发送层按通道渲染
（render_feishu 卡片含图片；render_plain 纯文本）。

LLM：job='intraday_report'，2 次调用（mood / mainline），_intraday_llm 缓存防抖。
旧模板可复用内容（温度/仓位/情绪人气/连板梯队/资金主线/板块异动/龙虎榜）按新结构取舍。
"""
from __future__ import annotations

import datetime as dt

from invest.db import connect

SKILL = {
    "id": "b1_intraday",
    "name": "盘中实时报告",
    "kind": "report",
    "description": "盘中实时报告：盘面总览(图表)/情绪判断+预测/日内主线/预案对照/核心关注",
    "uses": ["d8_temp_guide", "d9_rating_guide", "d11_emotion", "d12_limit_up_ladder",
             "d13_fund_line", "d21_freshness"],
    "params": {
        "db_path": "str, required",
        "public": "bool, optional, default False",
        "brief": "bool, optional, default True",
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
    # 结构分化：小盘(中证1000) vs 大盘(沪深300)
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


def _index_chart_data(rows: list[list[str]]) -> list[dict]:
    """指数表格行 → 条形图数据（name/value 百分比数字，去掉 % 符号）。"""
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
    """盘中涨停池统计（limit_up_pool 5 分钟落库）：涨停数/最高板/炸板率。"""
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
    """板块涨幅（industry_bars 最新收盘，方向参考）。"""
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
    """资金主线（sector_fund_flow 盘中实时净流入）。"""
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
    """连板梯队（limit_up_pool 盘中实时）。"""
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
    """核心关注实时行情：表格行 + 文本（供 LLM 输入）。"""
    conn = connect(db_path)
    try:
        core = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level IN ('core','track') "
            "AND out_date IS NULL ORDER BY level"
        )]
    finally:
        conn.close()
    if not core:
        return [], ""
    try:
        from invest.report import _live_quotes

        live, pct_map = _live_quotes(db_path, core)
    except Exception:
        return [], ""
    rows, lines = [], []
    for sym in core:
        price = live.get(sym)
        if price is None:
            continue
        pct = pct_map.get(sym)
        rows.append([sym, f"{price:.2f}", f"{pct:+.2%}" if pct is not None else "-"])
        lines.append(f"{sym} {price:.2f} ({pct:+.2%})" if pct is not None else f"{sym} {price:.2f}")
    return rows, "；".join(lines)


def _read_plan(conn) -> str:
    """盘后预案对照（预留，2026-08-22）：读 viewpoints source='plan' 最近 active。

    盘后复盘报告「预案模块」建成后写入该源，此处自动对照；无则省略。
    """
    try:
        row = conn.execute(
            """SELECT conclusion FROM viewpoints WHERE source='plan' AND status='active'
               ORDER BY created_at DESC LIMIT 3"""
        ).fetchall()
        return "\n".join(f"  - {r['conclusion']}" for r in row)
    except Exception:
        return ""


# ---------- 组装 ----------

def render(db_path: str, public: bool = False, brief: bool = True) -> dict:
    from invest.skills.sections import _intraday_llm

    sections: list[dict] = []
    now = dt.datetime.now()

    # ---- 1) 盘面总览 ----
    idx_rows, struct = _index_table()
    if idx_rows:
        sections.append({
            "type": "table", "title": "盘面总览",
            "columns": ["指数", "点位", "涨跌幅"], "rows": idx_rows,
        })
        if struct:
            sections.append({"type": "text", "text": struct})
        chart_data = _index_chart_data(idx_rows)
        if chart_data:
            sections.append({
                "type": "chart", "chart": "index_bars",
                "title": "主要指数涨跌幅（%）", "data": chart_data,
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
    else:
        # 规则回退：情绪周期 + 温度 + 连板
        parts = [f"温度 {temp_text}"]
        if emo_text:
            parts.append(emo_text)
        if lu_text:
            parts.append(f"盘中 {lu_text}")
        sections.append({"type": "text", "text": "**【情绪与预测】**\n" + "；".join(parts)})

    # ---- 3) 日内主线（LLM，失败回退直列） ----
    conn = connect(db_path)
    try:
        sector_top = _sector_top(conn)
        fund_top = _fund_top(conn)
        ladder = _ladder_text(conn)
    finally:
        conn.close()
    _core_rows, core_lines = _core_quotes(db_path)
    mainline = _intraday_llm.mainline_llm(db_path, {
        "sector_top": sector_top, "fund_top": fund_top,
        "ladder": ladder, "core": core_lines,
    })
    lines: list[str] = []
    for ml in (mainline.get("main_lines") or []):
        lines.append(f"**{ml.get('direction', '')}**")
        if ml.get("reason"):
            lines.append(f"  · 原因: {ml['reason']}")
        if ml.get("internal"):
            lines.append(f"  · 内部: {ml['internal']}")
        for ld in (ml.get("leaders") or []):
            lines.append(f"  · {ld.get('role', '')} {ld.get('name', '')}：{ld.get('analysis', '')}")
        if ml.get("outlook"):
            lines.append(f"  → 推演: {ml['outlook']}")
    if lines:
        sections.append({"type": "text", "text": "**【日内主线】**\n" + "\n".join(lines)})
    elif sector_top or fund_top:
        blocks = []
        if fund_top:
            blocks.append("资金主线(实时):\n" + fund_top)
        if sector_top:
            blocks.append("板块涨幅(收盘参考):\n" + sector_top)
        if ladder:
            blocks.append("连板梯队:\n" + ladder)
        sections.append({"type": "text", "text": "**【日内主线】**\n" + "\n\n".join(blocks)})

    # ---- 4) 预案对照（预留；盘后预案模块建成后自动生效） ----
    conn = connect(db_path)
    try:
        plan = _read_plan(conn)
    finally:
        conn.close()
    if plan:
        sections.append({"type": "text", "text": "**【与盘后预案对照】**\n" + plan})

    # ---- 5) 核心关注实时行情 ----
    if _core_rows:
        sections.append({
            "type": "table", "title": "核心关注实时行情",
            "columns": ["标的", "现价", "涨跌幅"], "rows": _core_rows,
        })
        if mainline.get("core_outlook"):
            sections.append({"type": "text", "text": f"**走势推演**: {mainline['core_outlook']}"})

    sections.append({
        "type": "text",
        "text": f"（数据失效即防守：行情不新鲜时不作 P0 决策）· {now.strftime('%H:%M')}",
    })
    return {"title": f"A股投资系统 · 盘中报告 {now.strftime('%H:%M')}", "sections": sections}
