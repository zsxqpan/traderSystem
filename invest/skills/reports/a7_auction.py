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
    },
}


def _index_table() -> tuple[list[list[str]], list[dict]]:
    """指数竞价表格行 + 图表数据。"""
    try:
        from invest.data.index_realtime import fetch_index_realtime

        idx = fetch_index_realtime()
    except Exception:
        return [], []
    order = ("000001", "399001", "000300", "000905", "000852", "000688", "399006", "899050")
    rows, chart = [], []
    for code in order:
        d = idx.get(code)
        if d:
            rows.append([d["name"], f"{d['price']:.2f}", f"{d['pct']:+.2f}%"])
            chart.append({"name": d["name"], "value": d["pct"]})
    return rows, chart


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
    """前一日热门板块核心股（2026-08-22）：昨日涨停股按东财行业(f100)聚合 TOP3，每板块取连板最高 1-2 只。

    返回 [{block, count, stocks: [{symbol, name, lianban}]}]。
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

    ind_map = fetch_industries([r["symbol"] for r in rows])
    blocks: dict[str, list] = {}
    for r in rows:
        ind = ind_map.get(r["symbol"]) or "其他"
        blocks.setdefault(ind, []).append(dict(r))
    top = sorted(blocks.items(), key=lambda kv: -len(kv[1]))[:3]
    out = []
    for ind, stocks in top:
        stocks.sort(key=lambda s: -(s.get("lianban") or 0))
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


def render(db_path: str) -> dict:
    from invest.skills.sections import _intraday_llm

    sections: list[dict] = []
    now = dt.datetime.now()

    # ---- 1) 指数竞价 ----
    idx_rows, chart = _index_table()
    if idx_rows:
        sections.append({
            "type": "table", "title": "指数竞价",
            "columns": ["指数", "竞价点位", "竞价涨跌幅"], "rows": idx_rows,
        })
        if chart:
            sections.append({
                "type": "chart", "chart": "index_bars",
                "title": "指数竞价涨跌幅（%）", "data": chart,
            })

    # ---- 2) 竞价异动榜 ----
    try:
        from invest.data.auction import fetch_top_gainers, fetch_top_losers, fetch_vol_top

        gainers = fetch_top_gainers(8)
        losers = fetch_top_losers(3)
        vol_top = fetch_vol_top(8)
    except Exception:
        gainers, losers, vol_top = [], [], []
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

    # ---- 3) 昨日连板今日竞价 ----
    conn = connect(db_path)
    try:
        ladder_symbols = _yesterday_ladder(conn)
    finally:
        conn.close()
    if ladder_symbols:
        try:
            from invest.data.auction import fetch_batch_quotes

            quotes = fetch_batch_quotes(ladder_symbols)
            ladder_rows = [
                [s, (quotes.get(s) or {}).get("name", ""),
                 f"{(quotes[s]['pct']):+.2f}%" if quotes.get(s) and quotes[s].get("pct") is not None else "-"]
                for s in ladder_symbols if s in quotes
            ]
            if ladder_rows:
                up = sum(1 for r in ladder_rows if r[2] not in ("-",) and r[2].startswith("+"))
                sections.append({
                    "type": "table", "title": f"昨日连板今日竞价（高开{up}/{len(ladder_rows)}=承接）",
                    "columns": ["代码", "名称", "竞价涨幅"], "rows": ladder_rows,
                })
        except Exception:
            pass

    # ---- 4) 市场关键股票竞价（前一日热门板块核心股 + 板块竞价解析） ----
    conn = connect(db_path)
    try:
        hot_blocks = _hot_core_stocks(conn)
    finally:
        conn.close()
    if hot_blocks:
        try:
            from invest.data.auction import fetch_batch_quotes

            core_syms = [s["symbol"] for b in hot_blocks for s in b["stocks"]]
            quotes = fetch_batch_quotes(core_syms)
            block_rows, block_texts = [], []
            for b in hot_blocks:
                for s in b["stocks"]:
                    q = quotes.get(s["symbol"])
                    pct_txt = (f"{(q['pct']):+.2f}%" if q and q.get("pct") is not None else "-")
                    block_rows.append([b["block"], f"{s['name']}({s['symbol']})",
                                       f"{int(s.get('lianban') or 0)}板", pct_txt])
                    block_texts.append(f"[{b['block']}] {s['name']} {pct_txt}")
            if block_rows:
                sections.append({
                    "type": "table", "title": "市场关键股票竞价（昨日热门板块核心股）",
                    "columns": ["板块", "核心股", "昨日", "竞价涨幅"], "rows": block_rows,
                })
            # LLM 板块竞价解析（与情绪预判独立，互不影响）
            ks = _intraday_llm.key_stock_llm(db_path, {"blocks_text": "\n".join(block_texts)})
            analysis_map = {b.get("name"): b.get("analysis") for b in (ks.get("blocks") or [])}
            lines = []
            for b in hot_blocks:
                a = analysis_map.get(b["block"])
                if a:
                    lines.append(f"  · **{b['block']}**（涨停{b['count']}家）: {a}")
            if lines:
                sections.append({
                    "type": "text", "text": "**【板块竞价解析】**\n" + "\n".join(lines),
                })
        except Exception:
            pass

    # ---- 5) 核心关注/持仓竞价 ----
    core = _core_symbols(db_path)
    if core:
        try:
            from invest.data.auction import fetch_batch_quotes

            quotes = fetch_batch_quotes(core)
            core_rows = [
                [s, (quotes.get(s) or {}).get("name", ""),
                 f"{(quotes[s]['price']):.2f}",
                 f"{(quotes[s]['pct']):+.2f}%" if quotes.get(s) and quotes[s].get("pct") is not None else "-"]
                for s in core if s in quotes
            ]
            if core_rows:
                sections.append({
                    "type": "table", "title": "核心关注/持仓竞价",
                    "columns": ["代码", "名称", "竞价价", "竞价涨幅"], "rows": core_rows,
                })
        except Exception:
            pass

    # ---- 5) 竞价情绪预判（LLM） ----
    index_text = " ".join(f"{r[0]} {r[2]}" for r in idx_rows) if idx_rows else ""
    gainers_text = "\n".join(
        f"  {g['name']} {g['pct']:+.2f}%" for g in gainers)
    vol_text = "\n".join(
        f"  {g['name']} {g['pct']:+.2f}% 竞价量{(g['vol'] or 0)/1e4:.0f}万手" for g in vol_top)
    ladder_text = "\n".join(
        f"  {r[0]} {r[2]}" for r in (ladder_rows if ladder_symbols else []))
    pred = _intraday_llm.auction_llm(db_path, {
        "index_text": index_text, "gainers": gainers_text,
        "vol_ratio": vol_text, "ladder": ladder_text,
    })
    if pred.get("mood"):
        lines = [f"**竞价情绪**: {pred.get('mood', '')}"]
        if pred.get("style"):
            lines.append(f"**风格预判**: {pred['style']}")
        if pred.get("hint"):
            lines.append(f"**操作提示**: {pred['hint']}")
        sections.append({"type": "text", "text": "**【竞价情绪预判】**\n" + "\n".join(lines)})
    elif idx_rows:
        sections.append({"type": "text", "text": f"**【竞价情绪预判】**（规则回退）指数竞价: {index_text}"})

    sections.append({
        "type": "text",
        "text": f"（9:25 集合竞价数据 · {now.strftime('%H:%M')} 生成）",
    })
    return {"title": f"A股投资系统 · 竞价报告 {now.strftime('%H:%M')}", "sections": sections}
