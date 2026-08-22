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
    "uses": ["d3_style", "d8_temp_guide", "d9_rating_guide", "d21_freshness", "d22_ratings",
             "d24_global_snapshot", "d25_overnight_analysis", "d26_market_watch",
             "d27_news_digest"],
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
    from invest.skills.sections._digest import digest, overnight_analysis

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
        "text": f"**【A股投资系统 · 盘前报告】**\n数据截至: {freshness}",
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

    # 8) 涨停异动监控（表格：停牌 + 风险提示/异动监控/暴雷）
    d = digest(db_path)
    watch_rows: list[list[str]] = []
    for h in fetch_halt_list():
        watch_rows.append(["停牌", f"{h['name']}({h['symbol']})", h.get("reason", "")[:30], "-"])
    for it in (d.get("risk_items") or []):
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
        sections.append({"type": "text", "text": "**【消息汇总】**（暂无素材或汇总失败）"})

    return {"title": "A股投资系统 · 盘前报告", "sections": sections}
