"""A0 盘前报告 skill（2026-08-22：A1 盘前清单 + A2 盘前信息早报合并版，结构化输出）。

render 返回 {"title": ..., "sections": [...]}，由发送层按通道渲染：
- 飞书：invest.push.render.render_feishu → interactive 卡片（表格/加粗）；
- 企微/微信：render.render_plain → 纯文本（表格转紧凑行）。

结构（10 节）：标题+数据截至 / 隔夜外围(表格,含日韩) / 外围影响(LLM) / 市场温度 /
仓位评级 / 市场风格 / 今日关注(Agent 8:30 落盘,仅结论) / 涨停异动监控(表格:停牌+风险提示+暴雷)
/ 风险提示(LLM) / 消息汇总(宏观仅变化时+个股+市场外,LLM)。

依赖：d24-d27 小节 skill 的底层逻辑（_digest / global_snapshot / halt），
本 skill 组装表格结构时直接使用底层函数（薄包装原则）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SKILL = {
    "id": "a0_premarket",
    "name": "盘前报告",
    "kind": "report",
    "description": "盘前报告（A1+A2 合并）：外围详情+LLM解读/温度仓位/风格/今日关注/涨停异动监控/消息汇总",
    "uses": ["d3_style", "d8_temp_guide", "d9_rating_guide", "d10_action_guide",
             "d21_freshness", "d22_ratings",
             "d24_global_snapshot", "d25_overnight_analysis", "d26_market_watch",
             "d27_news_digest", "d32_trade_signals", "d33_daily_actions"],
    "params": {
        "db_path": "str, required",
    },
}


def _read_agent_focus() -> str:
    """读 8:30 落盘的 Agent 关注方向（data/premarket_agent.txt，仅结论）。"""
    try:
        p = ROOT / "data" / "premarket_agent.txt"
        t = p.read_text(encoding="utf-8").strip()
        return t if t else ""
    except Exception:
        return ""


def render(db_path: str) -> dict:
    from invest.data.global_snapshot import global_snapshot_rows
    from invest.data.halt import fetch_halt_list
    from invest.db import connect
    from invest.report import _freshness, _rating_guide, _ratings, _style_block, _temp_guide
    from invest.skills.sections._digest import digest, digest_fallback_text, overnight_analysis

    sections: list[dict] = []

    # 1) 标题 + 数据截至
    conn = connect(db_path)
    try:
        freshness = _freshness(conn)
        row = conn.execute(
            "SELECT score FROM quant_temperature ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        score = float(row["score"]) if row and row["score"] is not None else None
    finally:
        conn.close()
    sections.append({
        "type": "text",
        "text": f"**【A股投资系统 · 盘前报告】**\n\n数据截至: {freshness}",
    })

    # 2) 隔夜外围（表格，含日韩；韩国/日本失败自动省略）
    rows = global_snapshot_rows()
    if rows:
        table_rows = []
        for r in rows:
            if r.get("pct") is not None:
                table_rows.append([r["name"], f"{r['pct']:+.2f}%"])
            elif r.get("value"):
                table_rows.append([r["name"], f"{r['value']:.4f}"])
        sections.append({
            "type": "table", "title": "隔夜外围",
            "columns": ["市场", "涨跌幅"], "rows": table_rows,
        })

    # 3) 外围影响（LLM 解读）
    oa = overnight_analysis(db_path)
    if oa:
        sections.append({"type": "text", "text": f"**【外围影响】**\n{oa}"})

    # 4) 市场温度 / 5) 仓位评级 / 6) 市场风格
    conn = connect(db_path)
    try:
        temp_txt = _temp_guide(score) if score is not None else "温度数据不足"
        sections.append({
            "type": "text",
            "text": f"**【市场温度】** {score:.0f}/100 → {temp_txt}" if score is not None
                    else "**【市场温度】** 暂无",
        })
        sections.append({"type": "text", "text": f"🎯 仓位: {_rating_guide(conn)}"})
        sections.append({"type": "text", "text": f"📈 评级: {_ratings(conn)}"})
        style = _style_block(conn)
        if style:
            # 2026-08-22：指数强弱榜归盘后日报，盘前只保留【市场风格】段
            style = style.split("【指数强弱榜】")[0].strip()
        if style:
            sections.append({"type": "text", "text": f"**【市场风格】** {style}"})
    finally:
        conn.close()

    # 7) 今日关注（Agent 8:30 落盘，仅结论）
    focus = _read_agent_focus()
    if focus:
        sections.append({"type": "text", "text": "**【今日关注】**\n" + focus})
    try:
        from invest.actions.format import action_table, pick_b1
        from invest.actions.query import list_actions
        from invest.actions.types import Action
        from invest.report import _action_guide

        conn_a = connect(db_path)
        try:
            raw = list_actions(conn_a)
            if score is not None:
                guide = _action_guide(conn_a, score)
                if guide:
                    sections.append({"type": "text", "text": f"📌 今日操作: {guide}"})
        finally:
            conn_a.close()
        acts = [Action(
            date=r.get("date") or "", symbol=r.get("symbol") or "",
            verb=r.get("verb") or "hold", priority=int(r.get("priority") or 2),
            source=r.get("source") or "", hint=r.get("hint") or "",
            entry_lo=r.get("entry_lo"), entry_hi=r.get("entry_hi"),
            stop_loss=r.get("stop_loss"), target=r.get("target"),
            status=r.get("status") or "pending",
        ) for r in raw]
        show = pick_b1(acts) if len(acts) > 12 else acts
        tbl = action_table(show, title="今日动作")
        if tbl:
            pool = set()
            try:
                conn_p = connect(db_path)
                try:
                    pool = {r["symbol"] for r in conn_p.execute(
                        "SELECT symbol FROM candidate_pool "
                        "WHERE level IN ('core','track') AND out_date IS NULL"
                    )}
                    pool |= {r["symbol"] for r in conn_p.execute(
                        "SELECT symbol FROM cards WHERE status IN ('locked','review')"
                    )}
                finally:
                    conn_p.close()
            except Exception:
                pool = set()
            tbl = {
                **tbl,
                "columns": list(tbl["columns"]) + ["池"],
                "rows": [
                    list(row) + (["池内"] if row[1] in pool else ["池外"])
                    for row in tbl["rows"]
                ],
            }
            sections.append(tbl)
    except Exception:
        pass
    try:
        from invest.signals.format import undigested_actions

        conn_u = connect(db_path)
        try:
            undig = undigested_actions(conn_u)
        finally:
            conn_u.close()
        if undig:
            sections.append({"type": "text", "text": "**【昨日信号】** " + undig})
    except Exception:
        pass
    try:
        from invest.signals.format import format_market_opportunity, rows_to_signals
        from invest.signals.query import list_signals

        conn_m = connect(db_path)
        try:
            mid_rows = list_signals(conn_m, horizon="mid", session="daily")
        finally:
            conn_m.close()
        opp = format_market_opportunity(rows_to_signals(mid_rows))
        if opp:
            title, _, rest = opp.partition("\n")
            sections.append({
                "type": "text",
                "text": f"**{title}**\n{rest}" if rest else f"**{title}**",
            })
    except Exception:
        pass

    # 8) 涨停异动监控（表格：停牌 + 风险提示/异动监控/暴雷）
    #    2026-09-18：风险条目只留**个股**（债券/汇率/商品/宏观等不进这张表）
    from invest.skills.sections.d26_market_watch import stock_risk_items

    d = digest(db_path)
    watch_rows: list[list[str]] = []
    for h in fetch_halt_list():
        watch_rows.append(["停牌", f"{h['name']}({h['symbol']})", h.get("reason", "")[:30], "-"])
    for it in stock_risk_items(d):
        watch_rows.append([
            it.get("kind", "风险"),
            f"{it.get('name', '')}({it.get('symbol', '')})",
            it.get("event", "")[:30],
            it.get("impact", "")[:20],
        ])
    if watch_rows:
        sections.append({
            "type": "table", "title": "涨停异动监控",
            "columns": ["类型", "标的", "事件", "影响"], "rows": watch_rows,
        })
    if d.get("risk_summary"):
        sections.append({"type": "text", "text": f"⚠️ 风险提示: {d['risk_summary']}"})

    # 9) 消息汇总（宏观仅变化时 + 个股 + 市场外）
    news = d.get("news") or {}
    news_lines: list[str] = []
    if d.get("macro_changed"):
        for it in (news.get("macro") or []):
            news_lines.append(f"  - 宏观: {it.get('title', '')}｜{it.get('impact', '')}")
    for grp, tag in (("stock", "个股"), ("market_outside", "市场外")):
        for it in (news.get(grp) or []):
            news_lines.append(f"  - {tag}: {it.get('title', '')}｜{it.get('impact', '')}")
    if news_lines:
        sections.append({"type": "text", "text": "**【消息汇总】**\n" + "\n".join(news_lines)})
    elif not d.get("ok"):
        # 2026-08-26：LLM 失败/素材空 → 降级直列最近电报，避免整节'暂无素材或汇总失败'
        fb = digest_fallback_text(db_path)
        if fb:
            sections.append({"type": "text",
                             "text": "**【消息汇总】**（LLM 汇总失败，直列原始电报素材）\n" + fb})
        else:
            sections.append({"type": "text", "text": "**【消息汇总】**（暂无素材或汇总失败）"})

    return {"title": "A股投资系统 · 盘前报告", "sections": sections}
