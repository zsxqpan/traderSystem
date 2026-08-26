"""D31 候选池预警 skill（2026-08-23：复用 trap-scan 完整 8 信号）。

- 扫描范围：候选池（out_date IS NULL）+ 持仓卡片（locked/review）去重；
- 硬信号（本地 0 联网）：⑤ K线异常配合（近5日涨幅≥15% / 涨停池上榜）、④ 基本面热度脱节（近似弱提示）；
- 软信号（web_search，合并 2 组关键词）：①低质同推 ②话术模板 ③付费社群 ⑥老师人设 ⑦跨平台联动 ⑧虚假研报；
- 评级：命中 0-1 🟢 / 2-3 🟡 / 4-5 🟠 / 6+ 🔴；trap_score 反向分（越高越安全）；
- scan_pool 供定时任务复用（写表+推送）；render 输出预警文本（仅 ≥🟡）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SKILL = {
    "id": "d31_pool_trap_alerts",
    "name": "候选池预警",
    "kind": "section",
    "description": "候选池/持仓逐股 8 信号杀猪盘扫描（硬信号本地、软信号搜索），输出预警文本；scan_pool 供定时任务复用",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "symbols": "list, optional（指定扫描标的；缺省=候选池+持仓去重）",
    },
}

SOFT_SCAN_DAILY = True  # 候选池 >10 只时置 False 降频（软信号每周）

# 软信号：2 组合并搜索，按关键词命中映射到信号
_SOFT_SEARCHES = [
    ("{name} 股票 推荐 暴涨 翻倍 老师 群 直播间",
     [("1", "低质同推", ("推荐", "暴涨", "翻倍")),
      ("2", "话术模板", ("暴涨", "翻倍", "建仓", "目标")),
      ("3", "付费社群", ("群", "直播间", "VIP", "收费")),
      ("6", "老师人设", ("老师", "股神", "带单"))]),
    ("{name} 抖音 小红书 知乎 辟谣 虚假",
     [("7", "跨平台联动", ("抖音", "小红书", "知乎", "B站")),
      ("8", "虚假研报", ("辟谣", "虚假", "谣言", "造假"))]),
]

_LEVELS = {"🟢": "安全", "🟡": "注意", "🟠": "警惕", "🔴": "高度可疑"}


def render(db_path: str, symbols: list | None = None) -> str:
    from invest.db import connect

    conn = connect(db_path)
    try:
        alerts = scan_pool(conn, symbols=symbols)
    finally:
        conn.close()
    warn = [a for a in alerts if a["level"] in ("🟡", "🟠", "🔴")]
    if not warn:
        return ""
    lines = ["【候选池预警 · 杀猪盘扫描】"]
    for a in warn:
        lines.append(f"  {a['symbol']} {a['name'] or ''} {a['level']} 命中{len(a['signals_hit'])}信号")
        for s in a["signals_hit"]:
            ev = (s.get("evidence") or "")[:80]
            lines.append(f"    · {s['name']}: {ev}")
    return "\n".join(lines)


def scan_pool(conn, symbols: list | None = None) -> list[dict]:
    """扫描候选池/持仓，返回全部预警结果 [{symbol, name, level, trap_score, signals_hit, recommendation}]。"""
    syms = _pool_symbols(conn) if symbols is None else [s for s in symbols if s]
    out = []
    for s in syms:
        try:
            hit = _scan_one(conn, s)
        except Exception as exc:
            logger.warning("trap scan %s 失败: %s", s, exc)
            continue
        level, score = _rate(hit["signals"])
        out.append({
            "symbol": s, "name": hit.get("name", ""), "level": level,
            "trap_score": score, "signals_hit": hit["signals"],
            "recommendation": _reco(level),
        })
    return out


def _pool_symbols(conn) -> list[str]:
    rows = conn.execute(
        "SELECT symbol FROM candidate_pool WHERE out_date IS NULL"
    ).fetchall()
    rows += conn.execute(
        "SELECT symbol FROM cards WHERE status IN ('locked','review')"
    ).fetchall()
    return list(dict.fromkeys(r["symbol"] for r in rows))


def _scan_one(conn, symbol: str) -> dict:
    """单票 8 信号扫描。返回 {symbol, name, signals: [{id,name,evidence,severity}]}。"""
    signals: list[dict] = []
    name = ""

    # 近 6 日收盘（信号5 K线异常）
    bars = conn.execute(
        """SELECT close FROM daily_bars WHERE symbol=?
           ORDER BY REPLACE(date,'-','') DESC LIMIT 6""",
        (symbol,),
    ).fetchall()
    closes = [float(b["close"]) for b in bars if b["close"] is not None]
    if len(closes) >= 6 and closes[0] > 0:
        pct5 = closes[0] / closes[5] - 1
        if pct5 >= 0.15:
            signals.append({"id": "5", "name": "K线异常配合",
                            "evidence": f"近5日涨幅{pct5:.1%}（≥15%）", "severity": "high"})

    # 涨停池上榜（最近两日，信号5 附加）
    try:
        lu = conn.execute(
            """SELECT 1 FROM limit_up_pool WHERE symbol=?
               AND date IN (SELECT DISTINCT date FROM limit_up_pool
                            ORDER BY date DESC LIMIT 2)""",
            (symbol,),
        ).fetchone()
        if lu:
            signals.append({"id": "5", "name": "K线异常配合",
                            "evidence": "近期涨停池上榜", "severity": "medium"})
    except Exception:
        pass

    # 龙虎榜上榜（近 10 日，信号4 热度佐证）
    try:
        lhb = conn.execute(
            """SELECT 1 FROM dragon_tiger WHERE symbol=?
               AND date >= date('now','localtime','-10 day') LIMIT 1""",
            (symbol,),
        ).fetchone()
        if lhb:
            signals.append({"id": "4", "name": "基本面热度脱节",
                            "evidence": "近10日龙虎榜上榜", "severity": "low"})
    except Exception:
        pass

    # 软信号（web_search，合并 2 组）
    if SOFT_SCAN_DAILY:
        signals.extend(_soft_hits(name or symbol))

    return {"symbol": symbol, "name": name, "signals": signals}


def _soft_hits(name: str) -> list[dict]:
    """软信号扫描：2 组搜索，按关键词命中映射信号。失败静默。"""
    hits: list[dict] = []
    for query_tpl, rules in _SOFT_SEARCHES:
        try:
            from invest.agent.web_tools import web_search

            r = web_search(query_tpl.format(name=name), n=5)
        except Exception as exc:
            logger.warning("trap 软信号搜索失败 %s: %s", name, exc)
            continue
        if not isinstance(r, list):
            continue
        blob = " ".join(f"{it.get('title', '')} {it.get('snippet', '')}" for it in r)
        for sig_id, sig_name, words in rules:
            if any(w in blob for w in words):
                hits.append({"id": sig_id, "name": sig_name,
                             "evidence": f"搜索到推广/联动内容: {blob[:60]}",
                             "severity": "medium"})
    return hits


def _rate(signals: list[dict]) -> tuple[str, float]:
    """命中信号数 → (等级, 反向分)。"""
    n = len(signals)
    if n <= 1:
        return "🟢", float(max(1, 10 - n))
    if n <= 3:
        return "🟡", float(max(1, 10 - n))
    if n <= 5:
        return "🟠", float(max(1, 10 - n))
    return "🔴", float(max(1, 10 - n))


def _reco(level: str) -> str:
    if level in ("🟠", "🔴"):
        return f"{level} 强烈建议谨慎/回避（{_LEVELS[level]}）"
    return f"{level} 有推广迹象，建议核实信息源（{_LEVELS[level]}）"
