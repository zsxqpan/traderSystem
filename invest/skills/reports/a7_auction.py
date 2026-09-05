"""A7 竞价报告 skill（2026-08-22：9:25 集合竞价结束后生成，飞书卡片）。

结构（雏形 v1，用户确认后迭代）：
1. 指数竞价：8 大指数竞价涨跌幅（高开/低开）+ 条形图；
2. 竞价异动榜：高开榜（抢筹信号）+ 量比榜（竞价放量）+ 低开榜（文本行）；
3. 昨日连板今日竞价：连板梯队竞价表现（高开=承接，低开=分歧）→ 情绪强弱；
4. 核心关注/持仓竞价：关注股竞价价/涨幅；
5. 竞价情绪预判（LLM，youzi 方法论）：情绪/风格/操作提示。

- 结构化输出（text/table/chart 节），发送层按通道渲染（飞书卡片 + 企微/微信纯文本）；
- 数据源：腾讯指数（9:25 后现价=竞价价）+ 东财 push2delay 全市场榜单 + 腾讯批量个股；
- LLM：job='auction'，1 次调用（auction_llm），失败省略预判节；
- 仅交易日 9:25-9:30 窗口运行（调度器保证），其余时段数据为收盘/盘中快照，勿误用。
"""
from __future__ import annotations

import datetime as dt

from invest.db import connect

SKILL = {
    "id": "a7_auction",
    "name": "竞价报告",
    "kind": "report",
    "description": "9:25 竞价报告：指数竞价/高开量比榜/连板竞价/核心关注竞价/情绪预判(LLM)",
    "uses": ["d12_limit_up_ladder", "d21_freshness"],
    "params": {
        "db_path": "str, required",
        "snapshot": "optional, 冻结快照；缺省则 render 内 freeze",
    },
}


_INDEX_ORDER = ("000001", "399001", "000300", "000905", "000852", "000688", "399006", "899050")


def _pct_txt(pct: float | None) -> str:
    if pct is None:
        return "-"
    return f"{pct:+.2%}"


def _index_table(results=None) -> tuple[list[list[str]], list[dict], list]:
    """指数竞价表格行 + 图表数据 + QuoteResult。"""
    from invest.data.quotes import INDEX_UNIVERSE, get_quotes, status_label

    if results is None:
        try:
            results = get_quotes(list(INDEX_UNIVERSE), obj_type="index")
        except Exception:
            return [], [], []
    by = {r.ref.symbol: r for r in results}
    rows, chart = [], []
    for code in _INDEX_ORDER:
        r = by.get(code)
        if r is None:
            continue
        price_txt = f"{r.price:.2f}" if r.price is not None else "—"
        rows.append([r.ref.name or code, price_txt, _pct_txt(r.pct), status_label(r)])
        if r.pct is not None:
            chart.append({"name": r.ref.name or code, "value": r.pct * 100.0})
    return rows, chart, results


def _format_rows(items: list[dict], with_vol: bool = False) -> list[list[str]]:
    """榜单表格行：高开/低开榜（代码/名称/竞价涨幅）或放量榜（+竞价量 万手）。"""
    rows = []
    for it in items:
        pct = it.get("pct")
        row = [it.get("symbol", ""), it.get("name", ""),
               f"{pct:+.2f}%" if pct is not None else "-"]
        if with_vol:
            vol = it.get("vol")
            row.append(f"{vol/1e4:.1f}万手" if vol is not None else "-")
        rows.append(row)
    return rows


def _yesterday_ladder(conn) -> list[str]:
    """昨日（最近交易日）连板股代码（limit_up_pool 非炸板）。"""
    try:
        rows = conn.execute(
            """SELECT symbol FROM limit_up_pool WHERE zhaban=0 AND date < ?
               ORDER BY date DESC LIMIT 30""",
            (dt.date.today().strftime("%Y%m%d"),),
        ).fetchall()
        seen: list[str] = []
        for r in rows:
            if r["symbol"] not in seen:
                seen.append(r["symbol"])
        return seen[:15]
    except Exception:
        return []


def _hot_core_stocks(conn) -> list[dict]:
    """前一日热门板块核心股（2026-08-22 v2）：昨日涨停股按东财行业(f100)聚合 TOP3，
    每板块选**成交量最大关注度最高**的 1-2 只（非连板最高——避免与「连板竞价」重合）。

    返回 [{block, count, stocks: [{symbol, name, lianban, volume}]}]。
    """
    try:
        rows = conn.execute(
            """SELECT symbol, name, lianban FROM limit_up_pool
               WHERE zhaban=0
                 AND date=(SELECT MAX(date) FROM limit_up_pool WHERE date < ?)
               ORDER BY lianban DESC LIMIT 50""",
            (dt.date.today().strftime("%Y%m%d"),),
        ).fetchall()
    except Exception:
        return []
    if not rows:
        return []
    from invest.data.auction import fetch_industries

    # 昨日成交量（关注度代理：成交量大=关注度高）
    vol_map: dict[str, float] = {}
    try:
        for r in rows:
            row = conn.execute(
                "SELECT volume FROM daily_bars WHERE symbol=? "
                "ORDER BY REPLACE(date,'-','') DESC LIMIT 1", (r["symbol"],),
            ).fetchone()
            vol_map[r["symbol"]] = float(row["volume"]) if row and row["volume"] else 0.0
    except Exception:
        pass
    ind_map = fetch_industries([r["symbol"] for r in rows])
    blocks: dict[str, list] = {}
    for r in rows:
        ind = ind_map.get(r["symbol"]) or "其他"
        item = dict(r)
        item["volume"] = vol_map.get(r["symbol"], 0.0)
        blocks.setdefault(ind, []).append(item)
    top = sorted(blocks.items(), key=lambda kv: -len(kv[1]))[:3]
    out = []
    for ind, stocks in top:
        stocks.sort(key=lambda s: -s.get("volume", 0.0))  # 成交量最大优先
        out.append({"block": ind, "count": len(stocks), "stocks": stocks[:2]})
    return out


def _core_symbols(db_path: str) -> list[str]:
    """核心关注 + 持仓（cards locked/review）。"""
    conn = connect(db_path)
    try:
        syms = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level IN ('core','track') AND out_date IS NULL"
        ).fetchall()]
        syms += [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM cards WHERE status IN ('locked','review')"
        ).fetchall()]
        return list(dict.fromkeys(syms))
    finally:
        conn.close()


def render(db_path: str, snapshot=None) -> dict:
    from invest.skills.sections import _intraday_llm
    from invest.skills.snapshot import freeze_snapshot

    sections: list[dict] = []
    snap = snapshot or freeze_snapshot("a7_auction", db_path)
    now = dt.datetime.now()
    as_of = snap.as_of

    # ========== 第一段：消费冻结快照（不再各自重拉） ==========
    idx_block = snap.blocks.get("index_quotes")
    idx_results = list(getattr(idx_block, "quotes", None) or [])
    idx_rows, chart, idx_results = _index_table(idx_results)

    boards = getattr(snap.blocks.get("auction_boards"), "payload", None) or {}
    gainers = list(boards.get("gainers") or [])
    losers = list(boards.get("losers") or [])
    vol_top = list(boards.get("vol_top") or [])

    from invest.data.quotes import status_label

    ladder_results = list(getattr(snap.blocks.get("ladder_quotes"), "quotes", None) or [])
    ladder_rows: list[list[str]] = []
    for r in ladder_results:
        ladder_rows.append([r.ref.symbol, r.ref.name, _pct_txt(r.pct), status_label(r)])

    key_block = snap.blocks.get("key_quotes")
    key_payload = getattr(key_block, "payload", None) or {}
    hot_blocks = key_payload.get("hot") if isinstance(key_payload, dict) else []
    key_quotes = {r.ref.symbol: r for r in (getattr(key_block, "quotes", None) or [])}
    block_rows, block_texts = [], []
    for b in hot_blocks or []:
        for s in b.get("stocks") or []:
            q = key_quotes.get(s["symbol"])
            pct_txt = _pct_txt(q.pct) if q else "—"
            label = status_label(q) if q else "源失败"
            block_rows.append([b["block"], f"{s['name']}({s['symbol']})",
                               f"{int(s.get('lianban') or 0)}板", pct_txt, label])
            block_texts.append(f"[{b['block']}] {s['name']} {pct_txt} {label}")

    core_results = list(getattr(snap.blocks.get("core_quotes"), "quotes", None) or [])
    core_rows: list[list[str]] = []
    for r in core_results:
        core_rows.append([
            r.ref.symbol, r.ref.name,
            f"{r.price:.2f}" if r.price is not None else "—",
            _pct_txt(r.pct), status_label(r),
        ])

    from invest.data.quotes import coverage_text, degrade_alert_text, report_should_degrade

    degrade, cov_info = report_should_degrade(idx_results, list(ladder_results) + list(core_results))
    sections.append({"type": "text", "text": coverage_text(idx_results, list(ladder_results) + list(core_results))})
    if degrade:
        sections.append({"type": "text", "text": degrade_alert_text(cov_info)})

    # ========== 第二段：LLM 解析（各模块一次调用）+ 情绪预判（独立） ==========
    index_text = " ".join(f"{r[0]} {r[2]}" for r in idx_rows) if idx_rows else ""
    boards_text = "\n".join(
        [f"  高开 {g['name']} {g['pct']:+.2f}%" for g in gainers]
        + [f"  放量 {g['name']} {g['pct']:+.2f}% 量{(g['vol'] or 0)/1e4:.0f}万手" for g in vol_top]
        + [f"  低开 {it['name']} {it['pct']:+.2f}%" for it in losers])
    ladder_text = "\n".join(f"  {r[0]} {r[2]}" for r in ladder_rows)
    key_text = "\n".join(block_texts)
    core_text = "\n".join(f"  {r[0]} {r[3]}" for r in core_rows)
    if degrade:
        analysis, pred = {}, {}
    else:
        analysis = _intraday_llm.section_analysis_llm(db_path, {
            "index_text": index_text, "boards_text": boards_text, "ladder_text": ladder_text,
            "key_text": key_text, "core_text": core_text,
        })
        pred = _intraday_llm.auction_llm(db_path, {
            "index_text": index_text, "gainers": boards_text,
            "vol_ratio": "\n".join(f"  {g['name']} {g['pct']:+.2f}%" for g in vol_top),
            "ladder": ladder_text,
        })

    def _an(key: str) -> str:
        """模块解析文案：无特别消息显示'（无特别消息）'。"""
        a = (analysis.get(key) or "").strip()
        return a if a and a != "无" else "（无特别消息）"

    # ========== 第三段：组装（表格 + 各自解析） ==========
    # 1) 指数竞价 + 解析
    if idx_rows:
        sections.append({
            "type": "table", "title": "指数竞价",
            "columns": ["指数", "竞价点位", "竞价涨跌幅", "状态"], "rows": idx_rows,
        })
        if chart:
            sections.append({
                "type": "chart", "chart": "index_bars",
                "title": "指数竞价涨跌幅（%）", "data": chart,
            })
        if analysis:
            sections.append({"type": "text", "text": f"**指数竞价解析**: {_an('index')}"})

    # 2) 高开放量榜 + 解析
    if gainers:
        sections.append({
            "type": "table", "title": "竞价高开榜（抢筹信号）",
            "columns": ["代码", "名称", "竞价涨幅"], "rows": _format_rows(gainers),
        })
    if vol_top:
        sections.append({
            "type": "table", "title": "竞价放量榜（抢筹）",
            "columns": ["代码", "名称", "竞价涨幅", "竞价量"], "rows": _format_rows(vol_top, with_vol=True),
        })
    if losers:
        sections.append({
            "type": "text",
            "text": "**竞价低开榜**: " + " ".join(
                f"{it['name']} {it['pct']:+.2f}%" for it in losers
                if it.get("pct") is not None),
        })
    if (gainers or vol_top or losers) and analysis:
        sections.append({"type": "text", "text": f"**高开放量榜解析**: {_an('boards')}"})

    # 3) 昨日连板竞价 + 解析
    if ladder_rows:
        up = sum(1 for r in ladder_rows if r[2] not in ("-",) and r[2].startswith("+"))
        sections.append({
            "type": "table", "title": f"昨日连板今日竞价（高开{up}/{len(ladder_rows)}=承接）",
            "columns": ["代码", "名称", "竞价涨幅", "状态"], "rows": ladder_rows,
        })
        if analysis:
            sections.append({"type": "text", "text": f"**连板竞价解析**: {_an('ladder')}"})

    # 4) 市场关键股票竞价 + 板块解析 + 模块解析
    if block_rows:
        sections.append({
            "type": "table", "title": "市场关键股票竞价（昨日热门板块核心股·成交量最大）",
            "columns": ["板块", "核心股", "昨日", "竞价涨幅", "状态"], "rows": block_rows,
        })
        ks = {} if degrade else _intraday_llm.key_stock_llm(
            db_path, {"blocks_text": "\n".join(block_texts)}
        )
        analysis_map = {b.get("name"): b.get("analysis") for b in (ks.get("blocks") or [])}
        lines = []
        for b in hot_blocks:
            a = analysis_map.get(b["block"])
            if a:
                lines.append(f"  · **{b['block']}**（涨停{b['count']}家）: {a}")
        if lines:
            sections.append({"type": "text", "text": "**【板块竞价解析】**\n" + "\n".join(lines)})
        if analysis:
            sections.append({"type": "text", "text": f"**关键股票竞价解析**: {_an('key_stocks')}"})

    # 5) 核心关注竞价 + 解析
    if core_rows:
        sections.append({
            "type": "table", "title": "核心关注/持仓竞价",
            "columns": ["代码", "名称", "竞价价", "竞价涨幅", "状态"], "rows": core_rows,
        })
        if analysis:
            sections.append({"type": "text", "text": f"**核心关注竞价解析**: {_an('core')}"})

    # 6) 竞价情绪预判（独立模块）
    if pred.get("mood"):
        lines = [f"**竞价情绪**: {pred.get('mood', '')}"]
        if pred.get("style"):
            lines.append(f"**风格预判**: {pred['style']}")
        if pred.get("hint"):
            lines.append(f"**操作提示**: {pred['hint']}")
        sections.append({"type": "text", "text": "**【竞价情绪预判】**\n" + "\n".join(lines)})
    elif idx_rows and not degrade:
        sections.append({"type": "text", "text": f"**【竞价情绪预判】**（规则回退）指数竞价: {index_text}"})

    stamp = as_of[11:16] if len(as_of) >= 16 else now.strftime("%H:%M")
    sections.append({
        "type": "text",
        "text": f"（9:25 集合竞价数据 · {stamp} 生成 · as_of {as_of}）",
    })
    # views：情绪预判 + 模块解析（发送层落库 source='auction_report'，供盘后复盘/竞价解读 skill 迭代）
    views = {"mood": pred, "analysis": analysis}
    from invest.skills.contract import check_completeness, get_manifest

    gate = check_completeness(get_manifest("a7_auction"), snap)
    return {"title": f"A股投资系统 · 竞价报告 {stamp}",
            "sections": sections, "views": views, "as_of": as_of,
            "completeness": {"status": gate.status, "detail": gate.detail,
                             "degrade": gate.degrade}}
