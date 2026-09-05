"""全链路流水线：采集 → 定量 → Agent → 推送（供调度器与手动脚本复用）。"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from invest.db import connect, init_db

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def build_collect_tasks(db_path: str) -> list[dict]:
    """基础任务 + 候选池个股批量日线任务。"""
    from invest.data.collector import TASKS
    tasks = list(TASKS)
    conn = connect(db_path)
    try:
        symbols = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE out_date IS NULL ORDER BY level, in_date"
        )]
    finally:
        conn.close()
    if symbols:
        tasks.append({
            "name": "stock_daily_all",
            "kind": "stock_daily_all",
            "table": "daily_bars",
            "sources": ["akshare", "tushare"],
            "cross_check": False,
            "params": {"symbols": symbols, "start_date": "20240101", "end_date": "20991231"},
        })
        tasks.append({
            "name": "seat_detail",
            "kind": "seat_detail",
            "table": "dragon_tiger",
            "sources": ["akshare"],
            "cross_check": False,
            "params": {"symbols": symbols, "days": 5},
        })
    return tasks


def collect_industry(db_path: str) -> list[dict]:
    """只采集行业指数全量（同花顺当天数据晚间才发布，供 21:30 刷新）。"""
    from invest.data.collector import TASKS, run_collection
    tasks = [t for t in TASKS if t["name"] == "industry_all"]
    return run_collection(db_path, tasks=tasks)


_INDEX_SUFFIXES = ("000016", "000905", "000852", "000688", "399006", "899050")


def collect_bars_and_indices(db_path: str) -> list[dict]:
    """晚间补采日线+指数（2026-08-17 修复数据滞后）。

    新浪/东财的当日日线、指数日线要到晚间才发布，16:00 收盘采集
    拿不到当天数据，导致 daily_bars/index_bars 滞后 1 个交易日。
    21:40 补采（此刻数据源已有当天数据），让 22:00 每日复盘用当天数据。
    """
    from invest.data.collector import TASKS, run_collection
    names = {"daily_bars", "index_bars"} | {f"index_bars_{s}" for s in _INDEX_SUFFIXES}
    tasks = [t for t in TASKS if t["name"] in names]
    return run_collection(db_path, tasks=tasks)


def collect(db_path: str, tasks=None) -> list[dict]:
    """采集数据（默认含候选池个股任务），返回任务摘要。"""
    from invest.data.collector import run_collection
    if tasks is None:
        tasks = build_collect_tasks(db_path)
    return run_collection(db_path, tasks=tasks)


def quant(db_path: str) -> dict:
    """计算并写入全部定量表，返回行数统计。"""
    from invest.quant.capital import compute_capital
    from invest.quant.crowding import compute_crowding
    from invest.quant.indicators import get_params
    from invest.quant.linkage import compute_linkage
    from invest.quant.macro_liquidity import compute_macro_liquidity
    from invest.quant.rotation import compute_rotation
    from invest.quant.strength import compute_strength
    from invest.quant.temperature import compute_temperature
    from invest.quant.weekly import compute_weekly

    init_db(db_path)
    conn = connect(db_path)
    try:
        ind = pd.read_sql_query(
            "SELECT date, industry, close, amount FROM industry_bars ORDER BY date", conn,
        )
        # 多指数读取（2026-08-16）：沪深300 作基准，其余指数用于风格/结构性行情判断
        idx_all = pd.read_sql_query(
            "SELECT date, index_code, close FROM index_bars ORDER BY date", conn,
        )
        idx300 = idx_all[idx_all["index_code"] == "000300"][["date", "close"]]
        idx = idx300  # 兼容下游（benchmark 用沪深300）
        macro = pd.read_sql_query(
            "SELECT date, indicator, value FROM macro_series ORDER BY date", conn,
        )
        emotion = pd.read_sql_query(
            "SELECT date, limit_up_count, max_lianban, zhaban_rate FROM market_emotion ORDER BY date", conn,
        )
        stock = pd.read_sql_query(
            "SELECT date, symbol, close FROM daily_bars ORDER BY date", conn,
        )
        valuation_hist = pd.read_sql_query(
            "SELECT date, industry, pe, pb FROM industry_valuation ORDER BY date", conn,
        )
    finally:
        conn.close()
    ind["date"] = pd.to_datetime(ind["date"], format="mixed", errors="coerce")
    idx["date"] = pd.to_datetime(idx["date"], format="mixed", errors="coerce")
    closes = ind.pivot_table(index="date", columns="industry", values="close")
    amounts = ind.pivot_table(index="date", columns="industry", values="amount")
    benchmark = idx.set_index("date")["close"]
    returns = closes.pct_change().replace([float("inf"), float("-inf")], float("nan"))
    returns = returns.mask(returns.abs() > 0.15)  # 单日>15% 视为数据异常（板块指数不可能），排除防污染

    stock["date"] = pd.to_datetime(stock["date"], format="mixed", errors="coerce")
    stock_closes = stock.pivot_table(index="date", columns="symbol", values="close")
    stock_results = {}
    if len(stock_closes.columns) >= 1:
        stock_returns = stock_closes.pct_change().replace([float("inf"), float("-inf")], float("nan"))
        stock_returns = stock_returns.mask(stock_returns.abs() > 0.15)
        conn2 = connect(db_path)
        try:
            seat_rows = pd.read_sql_query(
                "SELECT symbol, seat_type, buy, sell, net FROM dragon_tiger "
                "WHERE seat_type IS NOT NULL AND seat_type != 'list'", conn2,
            )
        finally:
            conn2.close()
        from invest.quant.capital import aggregate_fund_types, compute_stock_capital
        fund_types = aggregate_fund_types(seat_rows)
        stock_results["stock_strength"] = compute_strength(stock_closes, benchmark, obj_type="stock")
        stock_results["stock_weekly"] = compute_weekly(stock_closes, benchmark, obj_type="stock")
        stock_results["stock_capital"] = compute_stock_capital(stock_closes, stock_returns, fund_types)
        # Alpha158 核心量价因子（2026-08-16）：date×symbol 截面，供 factor_eval 检验
        try:
            from invest.quant.alpha158 import compute_alpha158
            stock_hist = stock.copy()
            stock_hist = stock_hist.sort_values(["symbol", "date"])
            fdf, fnames = compute_alpha158(stock_hist, idx)
            stock_results["alpha158"] = {"n_factors": len(fnames), "shape": list(fdf.shape)}
        except Exception:
            stock_results["alpha158"] = {"n_factors": 0, "error": "alpha158 计算失败"}
    if len(stock_closes.columns) >= 3:
        stock_results["stock_linkage"] = compute_linkage(stock_returns, **get_params("linkage"))

    # 指数相对强度（多指数 vs 沪深300，obj_type='index'）与风格判断（2026-08-16）
    index_results: dict = {}
    try:
        from invest.quant.style import compute_style, style_to_text
        idx_closes = idx_all.pivot_table(index="date", columns="index_code", values="close")
        idx_closes.index = pd.to_datetime(idx_closes.index, format="mixed", errors="coerce")
        idx_closes = idx_closes.dropna(how="all").sort_index()
        bench = benchmark  # 沪深300
        style_result = compute_style(idx_closes, bench)
        index_results["style"] = style_result
        index_results["style_text"] = style_to_text(style_result)
        # 各指数强度快照（写入 quant_strength, obj_type='index'）
        rows = []
        for code, v in (style_result.get("index_strength") or {}).items():
            if not v.get("rs") or v["rs"] != v["rs"]:  # NaN 跳过
                continue
            rows.append({
                "run_date": style_result.get("run_date"),
                "obj_type": "index",
                "obj": code,
                "period": "short",
                "rs": v["rs"],
                "momentum": v["momentum"],
                "trend_stage": v["trend_stage"],
                "calc_version": "v1",
            })
        if rows:
            index_results["index_strength_df"] = pd.DataFrame(rows)
    except Exception:
        logger.warning("指数风格计算失败", exc_info=True)

    results = {
        "strength": compute_strength(closes, benchmark),
        "weekly": compute_weekly(closes, benchmark),
        "rotation": compute_rotation(returns, amounts),
        "temperature": compute_temperature(returns, amounts, emotion=emotion),
        "capital": compute_capital(closes, returns),
        "linkage": compute_linkage(returns, **get_params("linkage")),
        "crowding": compute_crowding(amounts),
        "macro": compute_macro_liquidity(macro),
    }
    from invest.quant.valuation import (
        compute_pb_percentile,
        compute_pe_percentile,
        merge_valuation,
    )
    if not results["crowding"].empty and not valuation_hist.empty:
        pct = compute_pe_percentile(valuation_hist)
        results["crowding"] = merge_valuation(results["crowding"], pct, col_pct="pe_pct")
        # PB 分位（[A]1）：pb 数据存在时自动合并（数据源未接入时静默跳过）
        if valuation_hist["pb"].notna().any():
            pct_pb = compute_pb_percentile(valuation_hist)
            results["crowding"] = merge_valuation(results["crowding"], pct_pb, col_pct="pb_pct")
    # 拥挤度状态机（TODO 2.2）：crowding 分位 + 成交占比趋势 → 状态列
    try:
        from invest.quant.crowding_state import state_matrix
        if not results["crowding"].empty and not amounts.empty:
            states = state_matrix(results["crowding"], amounts)
            state_map = dict(zip(states["obj"], states["state"]))
            results["crowding"]["crowding_state"] = results["crowding"]["obj"].map(state_map)
    except Exception:
        logger.warning("拥挤度状态机计算失败", exc_info=True)

    conn = connect(db_path)
    try:
        from invest.data.storage import upsert_df
        upsert_df(conn, "quant_strength", results["strength"])
        upsert_df(conn, "quant_strength", results["weekly"])
        if "stock_strength" in stock_results:
            upsert_df(conn, "quant_strength", stock_results["stock_strength"])
        if "stock_weekly" in stock_results:
            upsert_df(conn, "quant_strength", stock_results["stock_weekly"])
        if "stock_linkage" in stock_results:
            upsert_df(conn, "quant_linkage", stock_results["stock_linkage"])
        if "stock_capital" in stock_results:
            upsert_df(conn, "quant_capital", stock_results["stock_capital"])
        if "index_strength_df" in index_results:
            upsert_df(conn, "quant_strength", index_results["index_strength_df"])
        upsert_df(conn, "quant_rotation", results["rotation"])
        upsert_df(conn, "quant_temperature", results["temperature"])
        upsert_df(conn, "quant_capital", results["capital"])
        upsert_df(conn, "quant_linkage", results["linkage"])
        upsert_df(conn, "quant_valuation", results["crowding"])
        upsert_df(conn, "quant_macro", results["macro"])
    finally:
        conn.close()
    results.update(stock_results)
    results["style_text"] = index_results.get("style_text", "")
    results["style"] = index_results.get("style", {})
    return {k: len(v) for k, v in results.items()}


def agent_premarket(db_path: str) -> str:
    """盘前关注方向（2026-08-22 精简：供 8:40 盘前报告 a0 的「今日关注」节，仅结论）。"""
    from invest.agent.agents import run_research
    conn = connect(db_path)
    try:
        return run_research(
            conn,
            "基于当前宏观流动性、市场温度与候选池，生成今日关注清单（最多5条方向）。"
            "本次输出用于盘前简报：每条方向只输出一行纯结论（不超过25字），"
            "不要依据、失效条件、宏观背景、周期标签——这些放复盘日报或个股分析。",
        )
    finally:
        conn.close()


def agent_after_close(db_path: str) -> str:
    from invest.agent.agents import run_trade
    conn = connect(db_path)
    try:
        return run_trade(conn, "复盘行业强度榜：识别强度异动行业，必要时发起归因请求，输出1-3条短线轨观点")
    finally:
        conn.close()


def arbitrate_all(db_path: str) -> int:
    from invest.agent.arbiter import arbitrate, find_conflicts
    conn = connect(db_path)
    try:
        pairs = find_conflicts(conn)
        for a, b in pairs:
            try:
                arbitrate(conn, a, b)
            except Exception as exc:
                logger.warning("仲裁 %s/%s 失败: %s", a, b, exc)
        return len(pairs)
    finally:
        conn.close()


# ---------- 消息模板 ----------
def _pct(v) -> str:
    return f"{v:+.1%}" if v is not None and pd.notna(v) else "-"


def _top_strength(conn, period: str = "short", n: int = 5) -> str:
    """行业强度榜文本；短线轨附 5/10/20 日相对强度（RS）。"""
    if period == "short":
        rows = conn.execute(
            """SELECT obj, rs, rs5, rs10, rs20, trend_stage FROM quant_strength
               WHERE period=? AND obj_type='industry'
                 AND run_date = (SELECT MAX(run_date) FROM quant_strength
                                 WHERE period=? AND obj_type='industry')
               ORDER BY rs DESC LIMIT ?""",
            (period, period, n),
        ).fetchall()
        parts = []
        for r in rows:
            windows = " ".join(
                f"{h}{_pct(r[k])}" for h, k in (("5日", "rs5"), ("10日", "rs10"), ("20日", "rs20"))
            )
            parts.append(f"{r['obj']} rs{_pct(r['rs'])} [{windows}] {r['trend_stage']}")
        return "\n".join(parts) or "-"
    rows = conn.execute(
        """SELECT obj, rs, trend_stage FROM quant_strength
           WHERE period=? AND obj_type='industry'
             AND run_date = (SELECT MAX(run_date) FROM quant_strength
                             WHERE period=? AND obj_type='industry')
           ORDER BY rs DESC LIMIT ?""",
        (period, period, n),
    ).fetchall()
    return "；".join(f"{r['obj']}({r['rs']:+.1%},{r['trend_stage']})" for r in rows) or "-"


def _movers(conn) -> list[dict]:
    """最新两个行业交易日各板块涨跌幅与前一日涨跌幅（供涨幅/跌幅榜与新晋标记）。"""
    rows = conn.execute(
        """WITH ranked AS (
             SELECT industry, close,
                    ROW_NUMBER() OVER (PARTITION BY industry ORDER BY REPLACE(date,'-','') DESC) rn
             FROM industry_bars
           )
           SELECT a.industry,
                  (a.close/b.close - 1) AS pct,
                  (b.close/c.close - 1) AS prev_pct
           FROM ranked a
           JOIN ranked b ON a.industry=b.industry AND b.rn=2
           JOIN ranked c ON a.industry=c.industry AND c.rn=3
           WHERE a.rn=1"""
    ).fetchall()
    return [dict(r) for r in rows]


def _mover_tag(industry: str, rs_map: dict, trend_map: dict, prev_top: set) -> str:
    """涨幅榜性质标签：趋势强 / 超跌反弹 / 新启动 / 新晋。"""
    tags = []
    rs5, _rs10, rs20 = rs_map.get(industry, (None, None, None))
    stage = trend_map.get(industry)
    if rs20 is not None and pd.notna(rs20):
        if rs20 > 0:
            tags.append("趋势强")
        elif rs5 is not None and pd.notna(rs5) and rs5 > 0:
            tags.append("超跌反弹")
    if stage == "启动":
        tags.append("新启动")
    if industry not in prev_top:
        tags.append("新晋")
    return "/".join(tags)


def _daily_movers_block(conn, n: int = 5) -> str:
    """当日板块涨幅/跌幅榜（各前 n）；涨幅榜附趋势性质与新晋标记。"""
    rows = _movers(conn)
    if not rows:
        return "当日涨幅前5:\n-\n当日跌幅前5:\n-"
    rs_rows = conn.execute(
        """SELECT obj, rs5, rs10, rs20, trend_stage FROM quant_strength
           WHERE period='short' AND obj_type='industry'
             AND run_date = (SELECT MAX(run_date) FROM quant_strength
                             WHERE period='short' AND obj_type='industry')"""
    ).fetchall()
    rs_map = {r["obj"]: (r["rs5"], r["rs10"], r["rs20"]) for r in rs_rows}
    trend_map = {r["obj"]: r["trend_stage"] for r in rs_rows}
    by_pct = sorted(rows, key=lambda r: r["pct"] if r["pct"] is not None else -1e9, reverse=True)
    prev_top = {
        r["industry"]
        for r in sorted(rows, key=lambda r: r["prev_pct"] if r["prev_pct"] is not None else -1e9, reverse=True)[:n]
    }
    up = []
    for r in by_pct[:n]:
        if r["pct"] is None:
            continue
        tag = _mover_tag(r["industry"], rs_map, trend_map, prev_top)
        up.append(f"{r['industry']} {r['pct']:+.2%}" + (f" [{tag}]" if tag else ""))
    down = [f"{r['industry']} {r['pct']:+.2%}" for r in by_pct[-n:][::-1] if r["pct"] is not None]
    return "当日涨幅前5:\n" + "\n".join(up or ["-"]) + "\n当日跌幅前5:\n" + "\n".join(down or ["-"])


def _breadth(conn) -> str:
    """最新行业交易日板块上涨/下跌家数。"""
    rows = _movers(conn)
    if not rows:
        return "-"
    up = sum(1 for r in rows if (r["pct"] or 0) > 0)
    down = sum(1 for r in rows if (r["pct"] or 0) < 0)
    return f"上涨{up}/下跌{down}"


def _temperature(conn) -> str:
    """市场温度：现值 + 较上日趋势 + 5/20日前 + 宽度。"""
    rows = conn.execute(
        "SELECT run_date, score, profit_effect FROM quant_temperature ORDER BY run_date DESC LIMIT 21"
    ).fetchall()
    if not rows:
        return "-"
    latest = rows[0]
    parts = [f"{latest['score']:.0f}/100"]
    if len(rows) >= 2:
        d = float(latest["score"]) - float(rows[1]["score"])
        trend = "升温" if d > 1 else ("降温" if d < -1 else "持平")
        parts.append(f"{trend}(较上日{d:+.0f})")
    if len(rows) >= 6:
        parts.append(f"5日前{rows[5]['score']:.0f}")
    if len(rows) >= 21:
        parts.append(f"20日前{rows[20]['score']:.0f}")
    if latest["profit_effect"] is not None:
        parts.append(f"宽度{latest['profit_effect']:.0%}")
    return " | ".join(parts)


def _freshness(conn) -> str:
    """数据新鲜度：板块/指数最新日期与滞后天数。"""
    import datetime as dt

    def _fmt(d) -> str:
        if not d:
            return "-"
        try:
            dd = dt.date.fromisoformat(d)
        except ValueError:
            try:
                dd = dt.date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:]}")
            except Exception:
                return str(d)
        days = (dt.date.today() - dd).days
        return f"{d}({'今日' if days <= 0 else f'滞后{days}天'})"

    ind = conn.execute("SELECT MAX(date) FROM industry_bars").fetchone()[0]
    idx = conn.execute("SELECT MAX(date) FROM index_bars").fetchone()[0]
    return f"板块{_fmt(ind)} | 指数{_fmt(idx)}"


def _ratings_block(conn) -> str:
    """评级 + 与上一评级日对比的变化。"""
    macro = conn.execute(
        "SELECT date, value FROM ratings WHERE kind='macro' ORDER BY date DESC LIMIT 2"
    ).fetchall()
    market = conn.execute(
        "SELECT date, value FROM ratings WHERE kind='market' ORDER BY date DESC LIMIT 2"
    ).fetchall()
    cur_macro = macro[0]["value"] if macro else None
    cur_market = market[0]["value"] if market else None
    base = f"宏观={cur_macro or '未评'} 市场={cur_market or '未评'}"
    changes = []
    if len(macro) == 2 and macro[0]["value"] != macro[1]["value"]:
        changes.append(f"宏观 {macro[1]['value']}→{macro[0]['value']}")
    if len(market) == 2 and market[0]["value"] != market[1]["value"]:
        changes.append(f"市场 {market[1]['value']}→{market[0]['value']}")
    if changes:
        base += f"（{'；'.join(changes)}）"
    return base


def _agent_viewpoints(conn, n: int = 5) -> str:
    """最新 active 的投研/交易观点（结论/周期/失效条件），替代原始大段文本。"""
    _period = {"micro": "超短", "short": "短线", "mid": "中线", "long": "长线"}
    rows = conn.execute(
        """SELECT obj, conclusion, period_tag, invalid_condition FROM viewpoints
           WHERE status='active' AND source IN ('research','trade')
           ORDER BY created_at DESC LIMIT ?""",
        (n,),
    ).fetchall()
    lines = []
    for r in rows:
        obj = f"[{r['obj']}] " if r["obj"] else ""
        period = _period.get(r["period_tag"], r["period_tag"] or "")
        fail = f"（失效:{r['invalid_condition']}）" if r["invalid_condition"] else ""
        lines.append(f"- {obj}{r['conclusion']} [{period}]{fail}")
    return "\n".join(lines)


def _send_structured(
    struct: dict,
    key: str,
    min_interval: float = 600.0,
    *,
    return_results: bool = False,
    message_kind: str = "report",
    message_id: str = "",
) -> bool | dict[str, bool]:
    """发送结构化报告；可返回逐通道结果，默认保持聚合 bool 兼容。"""
    from invest.config import get_settings
    from invest.notifier import Notifier
    from invest.push.feishu_push import send_card, send_text
    from invest.push.render import render_feishu, render_plain

    plain = render_plain(struct)
    stable_message_id = message_id or key
    results = {"feishu": False, "wecom": False, "weixin": False}
    card = render_feishu(struct)
    chat_id = getattr(get_settings(), "feishu_chat_id", "") or ""
    if chat_id:
        from invest.delivery import deliver_channel

        def _send_feishu() -> bool:
            return send_card(chat_id, "chat_id", card) or send_text(
                plain,
                key=key,
                min_interval=min_interval,
            )

        results["feishu"] = deliver_channel(
            "feishu",
            _send_feishu,
            message_kind=message_kind,
            message_id=stable_message_id,
        )
    other = Notifier().send_text(
        plain,
        key=key,
        min_interval=min_interval,
        feishu=False,
        return_results=True,
        message_kind=message_kind,
        message_id=stable_message_id,
    )
    if isinstance(other, dict):
        results.update(other)
    else:  # 兼容测试替身或旧式 Notifier 实现
        results["wecom"] = bool(other)
    return results if return_results else any(results.values())


def notify_morning_brief(
    db_path: str,
    *,
    return_results: bool = False,
) -> bool | dict[str, bool]:
    """盘前报告推送（2026-08-22：A1+A2 合并版 a0_premarket，飞书卡片 + 企微/微信纯文本）。"""
    from invest.skills.runner import run_structured

    struct = run_structured("a0_premarket", db_path=db_path)
    return _send_structured(
        struct,
        key="morning_brief",
        return_results=return_results,
        message_kind="report",
        message_id="a0_premarket",
    )


def notify_after_close(db_path: str, agent_text: str = "") -> bool:
    """盘后日报推送（2026-08-22：a3_daily 结构化，飞书卡片 + 企微/微信纯文本）。"""
    from invest.skills.runner import run_structured

    struct = run_structured("a3_daily", db_path=db_path, agent_text=agent_text)
    # 2026-08-22：明日预案落库（source='plan'，供 B1 对照/次日复盘）
    _persist_plan(struct.get("plan_data") or {})
    return _send_structured(struct, key="after_close")


def notify_auction(
    db_path: str,
    *,
    return_results: bool = False,
):
    """竞价报告：先完整性再生成/发送；回执走任务1账本。"""
    from invest.scheduler import JobResult
    from invest.skills.report_pipeline import deliver_report

    captured: dict = {}

    def send_fn(struct) -> bool:
        captured["struct"] = struct
        raw = _send_structured(
            struct,
            key="auction",
            return_results=True,
            message_kind="report",
            message_id="a7_auction",
        )
        results = raw if isinstance(raw, dict) else {"delivery": bool(raw)}
        captured["results"] = results
        return any(results.values())

    result = deliver_report("a7_auction", db_path, send_fn=send_fn)
    if result.status == "ok":
        _persist_auction_views(
            (captured.get("struct") or {}).get("views") or {},
            db_path=db_path,
        )
        channels = {
            channel: ("succeeded" if ok else "failed")
            for channel, ok in (captured.get("results") or {}).items()
        }
        result = JobResult.ok(
            "竞价报告投递成功", artifact="a7_auction", channel_results=channels,
        )
    elif result.status == "send_failed":
        channels = {
            channel: ("succeeded" if ok else "failed")
            for channel, ok in (captured.get("results") or {}).items()
        }
        if channels:
            result = JobResult(
                "send_failed", result.detail or "竞价报告投递失败",
                artifact="a7_auction", channel_results=channels,
            )
    return result if return_results else result.success


def _persist_auction_views(views: dict, *, db_path: str | None = None) -> None:
    """竞价报告观点/解析落库（2026-08-22）：viewpoints source='auction_report'。失败静默。"""
    if not views:
        return
    try:
        import json as _json

        from invest.db import connect as _connect

        target = db_path or str(Path(__file__).resolve().parents[1] / "data" / "invest.db")
        conn = _connect(target)
        try:
            for kind in ("mood", "analysis"):
                content = views.get(kind)
                if not content:
                    continue
                conn.execute(
                    """INSERT INTO viewpoints(source, conclusion, period_tag, confidence,
                       evidence_json, invalid_condition, status, created_at, obj_type, obj)
                       VALUES('auction_report', ?, 'micro', 0.5, '[]', '当日收盘复盘', 'active',
                              datetime('now','localtime'), 'market', ?)""",
                    (_json.dumps(content, ensure_ascii=False)[:2000], kind),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        import logging

        logging.getLogger(__name__).warning("竞价观点落库失败", exc_info=True)


def _persist_plan(plan_data: dict) -> None:
    """明日预案落库（2026-08-22）：viewpoints source='plan'，conclusion=JSON。失败静默。"""
    if not plan_data:
        return
    try:
        import json as _json

        from invest.db import connect as _connect

        conn = _connect(str(Path(__file__).resolve().parents[1] / "data" / "invest.db"))
        try:
            conn.execute(
                """INSERT INTO viewpoints(source, conclusion, period_tag, confidence,
                   evidence_json, invalid_condition, status, created_at, obj_type, obj)
                   VALUES('plan', ?, 'short', 0.5, '[]', '次日盘面验证', 'active',
                          datetime('now','localtime'), 'market', 'plan')""",
                (_json.dumps(plan_data, ensure_ascii=False)[:2000],),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        import logging

        logging.getLogger(__name__).warning("明日预案落库失败", exc_info=True)


def notify_weekend(
    db_path: str,
    agent_text: str = "",
    *,
    return_results: bool = False,
) -> bool | dict[str, bool]:
    """周报推送（2026-08-22：经 Skill Runner 调 a4_weekly）。"""
    from invest.skills.runner import run as run_skill
    msg = run_skill("a4_weekly", db_path=db_path, agent_text=agent_text)
    from invest.notifier import Notifier
    raw = Notifier().send_text(
        msg,
        key="weekend",
        min_interval=600,
        return_results=return_results,
        message_kind="report",
        message_id="a4_weekly",
    )
    if return_results:
        return raw
    return any(raw.values()) if isinstance(raw, dict) else bool(raw)


def _macro_text(conn) -> str:
    rows = conn.execute(
        "SELECT indicator, value FROM quant_macro ORDER BY date DESC, indicator"
    ).fetchall()
    return "；".join(f"{r['indicator']}={r['value']}" for r in rows) or "-"


# A7 P2 例行简报已于 2026-08-22 删除（内容与 22:00 合并版盘后日报重复、无任务调度）


# ---------- 收盘快照（2026-08-20 方案3：16:10 实时源直接落当日收盘价） ----------

# 腾讯指数快照代码：index_code -> qt.gtimg.cn 代码
INDEX_TENCENT_CODES = {
    "000300": "s_sh000300",  # 沪深300
    "000001": "s_sh000001",  # 上证指数
    "000016": "s_sh000016",  # 上证50
    "000905": "s_sh000905",  # 中证500
    "000852": "s_sh000852",  # 中证1000
    "000688": "s_sh000688",  # 科创50
    "399001": "s_sz399001",  # 深证成指
    "399006": "s_sz399006",  # 创业板指
    "899050": "s_bj899050",  # 北证50
}


def _fetch_index_closes(today) -> list[dict]:
    """腾讯指数快照（GBK，~分割，[2]=代码 [3]=现价）→ index_bars 行。失败返回空列表。"""
    import urllib.request

    codes = ",".join(INDEX_TENCENT_CODES.values())
    url = f"https://qt.gtimg.cn/q={codes}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/",
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理
        with opener.open(req, timeout=10) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
        rows = []
        for line in raw.split(";"):
            line = line.strip()
            if "=" not in line:
                continue
            val = line.split("=", 1)[1].strip().strip('"')
            parts = val.split("~")
            if len(parts) < 4:
                continue
            try:
                price = float(parts[3])
            except (TypeError, ValueError):
                continue
            code = parts[2]
            if code in INDEX_TENCENT_CODES and price > 0:
                rows.append({"index_code": code, "date": today.isoformat(),
                             "close": price, "src": "snapshot"})
        return rows
    except Exception as exc:
        logger.warning("指数收盘快照失败: %s", exc)
        return []


def snapshot_close(db_path: str) -> dict:
    """收盘快照落库（2026-08-20 初版；2026-08-24 升级为「收盘即日线」）。

    交易日 15:10 执行，不必等 akshare 日线晚间（约 21 点）才发布：
    1. 核心池（core/track）个股 → daily_bars（src='snapshot'，实时源收盘价）；
    2. **全市场当日完整 OHLCV**（东财 clist 批量接口，收盘后即有）→ daily_bars（src='snapshot'）；
    3. 指数（腾讯快照）→ index_bars（src='snapshot'）。
    晚间 akshare 权威日线写入后自动删除当日 snapshot 行（见 collector._run_one），不会双行。
    返回 {stock: 写入数, index: 写入数, skipped?: 原因}。
    """
    import datetime as dt

    from invest.data.calendar import is_trading_day

    today = dt.date.today()
    if not is_trading_day(today):
        return {"skipped": "非交易日"}
    from invest.data.storage import upsert_df
    from invest.intraday import _bare_symbol

    conn = connect(db_path)
    try:
        # 1) 核心池个股收盘（实时快照源，15:00 后即当日收盘价）
        core = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE level IN ('core','track') AND out_date IS NULL"
        ).fetchall()]
        stock_rows: list[dict] = []
        if core:
            try:
                from invest.data.realtime import RealtimeQuoter

                with RealtimeQuoter() as q:
                    quotes = q.fetch(core)
                for msym, qq in quotes.items():
                    if qq.price is None:
                        continue
                    stock_rows.append({
                        "symbol": _bare_symbol(msym), "date": today.isoformat(),
                        "close": float(qq.price), "src": "snapshot",
                    })
            except Exception as exc:
                logger.warning("核心池收盘快照失败: %s", exc)
        n_core = len(stock_rows)
        if stock_rows:
            upsert_df(conn, "daily_bars", pd.DataFrame(stock_rows))

        # 2) 全市场收盘即日线（2026-08-24：东财 clist 批量，收盘后即有完整 OHLCV）
        n_market = 0
        try:
            from invest.data.close_daily import fetch_all_close_daily

            market = fetch_all_close_daily(today.isoformat())
            if not market.empty:
                market = market.copy()
                market["src"] = "snapshot"
                n_market = upsert_df(conn, "daily_bars", market)
        except Exception as exc:
            logger.warning("全市场收盘日线失败: %s", exc)

        # 3) 指数收盘（腾讯快照）
        idx_rows = _fetch_index_closes(today)
        n_idx = len(idx_rows)
        if idx_rows:
            upsert_df(conn, "index_bars", pd.DataFrame(idx_rows))
        return {"stock": n_core, "market": n_market, "index": n_idx}
    finally:
        conn.close()