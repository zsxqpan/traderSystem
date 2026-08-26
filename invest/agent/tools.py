"""Agent 工具注册表：定量层查询 + 观点写入 + 工单发送。"""
from __future__ import annotations

import datetime as dt
import functools
import logging
import sqlite3
import threading
import time

from invest.agent import tickets as ticket_mod

logger = logging.getLogger(__name__)


def _query(conn: sqlite3.Connection, sql: str, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


# ---------- 个股日线（按需） ----------

_stock_daily_cache: dict[str, tuple[float, dict]] = {}
_STOCK_DAILY_TTL = 1800.0  # 缓存 30 分钟


def _norm_symbol(symbol: str) -> str:
    """归一化为 6 位股票代码（容忍 '600519.SH' / '600519' / 空格）。"""
    s = (symbol or "").strip().upper()
    s = s.split(".")[0] if "." in s else s
    s = "".join(ch for ch in s if ch.isdigit())
    return s if len(s) == 6 else ""


def _daily_stats(symbol: str, source: str, rows: list[tuple]) -> dict:
    """由 [(date, close), ...]（按日期倒序）计算统计。"""
    dates = [str(d) for d, _ in rows]
    closes = [float(c) for _, c in rows if c is not None]
    if not closes or closes[0] <= 0:
        return {"symbol": symbol, "source": source, "error": "无有效收盘数据"}

    def pct_n(n: int):
        return round(closes[0] / closes[n] - 1, 4) if len(closes) > n and closes[n] > 0 else None

    return {
        "symbol": symbol,
        "source": source,  # db=本地库 / akshare=按需联网
        "latest_date": dates[0],
        "latest_close": round(closes[0], 2),
        "pct_1d": pct_n(1),
        "pct_5d": pct_n(5),
        "pct_20d": pct_n(20),
        "high_60d": round(max(closes), 2),
        "low_60d": round(min(closes), 2),
        "last_rows": [{"date": dates[i], "close": round(closes[i], 2)} for i in range(min(5, len(closes)))],
    }


def _realtime_close(symbol: str) -> float | None:
    """三源实时价（盘中=现价、收盘后=收盘价），失败返回 None。"""
    try:
        from invest.data.realtime import RealtimeQuoter

        with RealtimeQuoter() as q:
            quotes = q.fetch([symbol])
        for qq in quotes.values():
            if qq.price and qq.price > 0:
                return float(qq.price)
    except Exception:
        pass
    return None


def xueqiu_fetch_user(conn, user_id: str, limit: int = 10) -> dict:
    """抓雪球用户主页动态（2026-08-25，Playwright 过 WAF）。

    自动 upsert big_v_profile（id=xq_{uid}，homepage 指向主页）；返回动态列表 [{url, title}]。
    模型可选取文章 URL 再调 xueqiu_fetch_article 抓正文入库。失败返回 {error}。
    """
    from invest.data.storage import upsert_df
    from invest.data.xueqiu_fetch import fetch_user_statuses

    uid = str(user_id or "").strip()
    if not uid or not uid.isdigit():
        return {"error": "请提供雪球用户 ID（数字）或主页 URL"}
    import datetime as _dt

    import pandas as _pd

    rows = fetch_user_statuses(uid, limit=limit)
    if not rows:
        return {"error": "雪球主页抓取失败（WAF 或用户不存在）"}
    # 建/更新画像（id 与主页固定；风格/擅长等由 big-v-monitor 后续补充）
    pid = f"xq_{uid}"
    try:
        upsert_df(conn, "big_v_profile", _pd.DataFrame([{
            "id": pid, "name": pid, "platform": "xueqiu",
            "xueqiu_id": uid, "homepage": f"https://xueqiu.com/u/{uid}",
            "updated_at": _dt.date.today().isoformat(),
        }]))
    except Exception as exc:
        logger.warning("大V画像写入失败 %s: %s", pid, exc)
    return {"ok": True, "profile_id": pid, "statuses": rows[:limit]}


def xueqiu_fetch_article(conn, url: str, profile_id: str = "") -> dict:
    """抓雪球文章正文（2026-08-25，Playwright 过 WAF）并入库 big_v_opinion（按 url 去重）。

    profile_id 需已存在（先 xueqiu_fetch_user 建画像）；不提供时仅返回内容不入库。
    返回 {ok, title, time, author, text}；失败 {error}。
    """
    from invest.data.storage import upsert_df
    from invest.data.xueqiu_fetch import fetch_article

    art = fetch_article(url)
    if not art:
        return {"error": "雪球文章抓取失败（WAF 或页面结构变化）"}
    if profile_id:
        exist = conn.execute("SELECT 1 FROM big_v_profile WHERE id=?", (profile_id,)).fetchone()
        if not exist:
            return {"error": f"画像不存在: {profile_id}，请先 xueqiu_fetch_user 建画像"}
        dup = conn.execute("SELECT 1 FROM big_v_opinion WHERE url=? LIMIT 1", (url,)).fetchone()
        if not dup:
            try:
                import pandas as _pd

                upsert_df(conn, "big_v_opinion", _pd.DataFrame([{
                    "profile_id": profile_id,
                    "opinion_date": (art.get("time") or "")[:10] or "2026-08-25",
                    "symbol": "", "topic": (art.get("title") or "")[:100],
                    "view": (art.get("text") or "")[:500],
                    "bias": "", "confidence": None, "url": url,
                }]))
            except Exception as exc:
                logger.warning("观点入库失败 %s: %s", url[:40], exc)
    return {"ok": True, "title": art.get("title"), "time": art.get("time"),
            "author": art.get("author"), "text": (art.get("text") or "")[:2000]}


def query_lhb(conn: sqlite3.Connection, symbol: str = "", name: str = "", n: int = 5) -> dict:
    """查询个股龙虎榜（2026-08-25）：本地 dragon_tiger 表按股票/名称查最近 N 次。

    返回 {symbol?, name?, rows: [{date, name, buy, sell, net}]}；无数据 rows=[]（不报错）。
    优先本地数据（akshare 已采集），搜不到才需要 web_search 补最新。
    """
    sym = _norm_symbol(symbol) if symbol else ""
    cond, args = "1=1", []
    if sym:
        cond, args = "symbol=?", [sym]
    elif name:
        cond, args = "name LIKE ?", [f"%{name}%"]
    else:
        return {"error": "请提供股票代码（symbol）或名称（name）"}
    n = max(1, min(int(n or 5), 20))
    try:
        rows = _query(
            conn,
            f"""SELECT DISTINCT date, name, buy, sell, net FROM dragon_tiger
                WHERE {cond} AND net IS NOT NULL AND seat_type='list'
                ORDER BY date DESC LIMIT ?""",
            (*args, n),
        )
    except Exception:
        rows = []
    return {"symbol": sym, "name": name, "rows": rows}


def query_realtime_quote(conn=None, symbol: str = "", obj_type: str = "stock",
                         symbols: list | None = None) -> dict:
    """实时报价（2026-08-25）：个股/指数/ETF 三源实时快照。

    - obj_type=stock：RealtimeQuoter 三源（新浪→腾讯→东财 push2），盘中=现价，收盘后=收盘价；
    - obj_type=index：腾讯指数快照；obj_type=etf：东财 ETF 快照；
    实时数据即最新，**不受 daily_bars 新鲜度守卫约束**。
    返回 {obj_type, quotes: {code: {name?, price, pct?, ...}}}；失败 {error}。
    """
    out: dict = {"obj_type": obj_type, "quotes": {}}
    try:
        if obj_type == "index":
            from invest.data.index_realtime import fetch_index_realtime

            for code, d in fetch_index_realtime().items():
                out["quotes"][code] = {"name": d.get("name"), "price": d.get("price"),
                                       "pct": d.get("pct")}
            return out
        if obj_type == "etf":
            from invest.data.etf import INDEX_ETFS, fetch_etf_quotes

            codes = symbols or list(INDEX_ETFS)
            for code, q in fetch_etf_quotes(codes).items():
                out["quotes"][code] = {
                    "name": q.get("name") or code, "price": q.get("price"), "pct": q.get("pct"),
                    "amount": q.get("amount"), "vol_ratio": q.get("vol_ratio"),
                    "main_net": q.get("main_net"), "super_net": q.get("super_net"),
                }
            return out
        from invest.data.realtime import RealtimeQuoter
        from invest.intraday import _bare_symbol

        target = list(dict.fromkeys(symbols or ([symbol] if symbol else [])))
        if not target:
            return {"error": "请提供 symbol（个股代码）或 symbols 列表"}
        with RealtimeQuoter() as q:
            quotes = q.fetch(target)
        for msym, qq in quotes.items():
            out["quotes"][_bare_symbol(msym)] = {
                "price": qq.price, "pct": qq.pct,
                "ts": qq.ts.isoformat() if qq.ts else None, "src": qq.src,
            }
        if not out["quotes"]:
            return {"error": "未取到实时报价"}
        return out
    except Exception as exc:
        return {"error": f"实时报价失败: {type(exc).__name__}: {exc}"}


def query_stock_daily(conn: sqlite3.Connection, symbol: str, days: int = 60) -> dict:
    """查询个股日线（最近收盘/涨跌幅/区间高低）。

    优先读本地 daily_bars；本地有历史但缺当日时（2026-08-25）：**三源实时接口拼当日**
    （盘中=现价、收盘后=收盘价），不等 akshare 历史 K 线晚间更新；
    本地完全缺失时仍按需 akshare（东财→新浪双源回退，30 分钟缓存）。
    周期建议（2026-08-18）：短线/游资视角 days=60；中线 days=250；长线 days=500。
    返回 {symbol, latest_close, latest_date, pct_1d/5d/20d, high/low(窗口内), last_rows}。
    """
    sym = _norm_symbol(symbol)
    if not sym:
        return {"error": "请提供 6 位股票代码（如 600519）"}
    days = max(20, min(int(days or 60), 750))

    now = time.time()
    cached = _stock_daily_cache.get(sym)
    if cached and now - cached[0] < _STOCK_DAILY_TTL:
        return cached[1]

    rows = _query(
        conn,
        "SELECT date, close FROM daily_bars WHERE symbol=? ORDER BY date DESC LIMIT ?",
        (sym, days),
    )
    # 2026-08-25：本地历史 + 三源实时拼当日（收盘后=收盘价），不等晚间 akshare
    if rows:
        rt_close = _realtime_close(sym)
        if rt_close is not None:
            today = dt.date.today().isoformat()
            merged = [(today, rt_close)] + [(r["date"], r["close"]) for r in rows if r["date"] != today]
            if len(merged) >= 2:
                out = _daily_stats(sym, "db+realtime", merged)
                _stock_daily_cache[sym] = (now, out)
                return out
    if rows and len(rows) >= 5:
        out = _daily_stats(sym, "db", [(r["date"], r["close"]) for r in rows])
        _stock_daily_cache[sym] = (now, out)
        return out

    # 本地缺失/样本太少 → 按需联网（东财 → 新浪 双源回退，与采集层一致）
    try:
        import akshare as ak

        end = dt.date.today().isoformat().replace("-", "")
        start = (dt.date.today() - dt.timedelta(days=days * 2)).isoformat().replace("-", "")
        df = None
        em_exc = sina_exc = None
        try:
            _df = ak.stock_zh_a_hist(symbol=sym, period="daily",
                                     start_date=start, end_date=end, adjust="qfq")
            if _df is not None and not _df.empty and "收盘" in _df.columns:
                df = _df
        except Exception as exc:
            em_exc = exc
        if df is None:
            try:
                prefix = "sh" if sym.startswith(("6", "9")) else "sz"
                _df = ak.stock_zh_a_daily(symbol=prefix + sym, adjust="qfq")
                if _df is not None and not _df.empty:
                    # 新浪 date 可能是 datetime：统一归一化为 YYYYMMDD 再按起始日过滤
                    _dates = _df["date"].astype(str).str[:10].str.replace("-", "")
                    _df = _df[_dates >= start]
                    df = _df
            except Exception as exc:
                sina_exc = exc
        if df is None or df.empty:
            err = "未查到日线数据（代码可能错误或新股）"
            if em_exc or sina_exc:
                err += f"；东财: {type(em_exc).__name__ if em_exc else '-'}；新浪: {type(sina_exc).__name__ if sina_exc else '-'}"
            out = {"symbol": sym, "source": "akshare", "error": err}
        else:
            # 兼容东财(日期/收盘) 与 新浪(date/close) 两套列名
            date_col = "日期" if "日期" in df.columns else "date"
            close_col = "收盘" if "收盘" in df.columns else "close"
            sub = df.sort_values(date_col, ascending=False).head(days)
            rows2 = [(str(r[date_col])[:10], float(r[close_col])) for _, r in sub.iterrows()]
            out = _daily_stats(sym, "akshare", rows2)
        _stock_daily_cache[sym] = (now, out)
        return out
    except Exception as exc:
        return {"symbol": sym, "source": "akshare", "error": f"日线获取失败: {type(exc).__name__}: {exc}"}


# ---------- 查询工具 ----------
def query_strength(conn, period: str = "short", top: int = 10, obj_type: str = "industry") -> list[dict]:
    return _query(
        conn,
        """SELECT obj, rs, momentum, trend_stage FROM quant_strength
           WHERE period=? AND obj_type=?
             AND run_date = (SELECT MAX(run_date) FROM quant_strength
                             WHERE period=? AND obj_type=?)
           ORDER BY rs DESC LIMIT ?""",
        (period, obj_type, period, obj_type, top),
    )


def query_rotation(conn, top: int = 10) -> list[dict]:
    return _query(conn, "SELECT industry, rank, lead_lag, turnover_share FROM quant_rotation WHERE run_date = (SELECT MAX(run_date) FROM quant_rotation) ORDER BY rank LIMIT ?", (top,))


def query_temperature(conn) -> list[dict]:
    return _query(conn, "SELECT run_date, profit_effect, score FROM quant_temperature ORDER BY run_date DESC LIMIT 1")


def query_capital(conn) -> list[dict]:
    return _query(conn, "SELECT obj, fund_type, style, confidence FROM quant_capital q WHERE run_date = (SELECT MAX(run_date) FROM quant_capital q2 WHERE q2.obj_type = q.obj_type) ORDER BY confidence DESC")


def query_linkage(conn, threshold: float = 0.8, top: int = 10) -> list[dict]:
    return _query(conn, "SELECT a, b, corr, lead FROM quant_linkage WHERE run_date = (SELECT MAX(run_date) FROM quant_linkage) AND corr>=? ORDER BY corr DESC LIMIT ?", (threshold, top))


def query_macro(conn) -> list[dict]:
    return _query(conn, "SELECT date, indicator, value FROM quant_macro ORDER BY date DESC, indicator")


def query_pool(conn) -> list[dict]:
    return _query(conn, "SELECT symbol, level, reason, falsify_condition FROM candidate_pool WHERE out_date IS NULL ORDER BY level, in_date")


# ---------- 写入工具 ----------
def write_viewpoint(
    conn,
    source: str,
    conclusion: str,
    period_tag: str,
    confidence: float,
    evidence: list,
    invalid_condition: str,
    obj_type: str = "",
    obj: str = "",
) -> dict:
    from invest.viewpoints.store import create_viewpoint
    vid = create_viewpoint(
        conn, source=source, conclusion=conclusion, period_tag=period_tag,
        confidence=confidence, evidence=evidence, invalid_condition=invalid_condition,
        obj_type=obj_type, obj=obj,
    )
    return {"viewpoint_id": vid}



def query_realtime_health(conn) -> dict:
    """查询实时行情数据健康状态（数据失效即防守：ok=False 时禁止基于实时价做 P0 决策）。

    2026-08-18 改：**交易时段感知**——非交易时段休市，行情旧属正常，不视为数据失效：
    返回 ok=True 并提示改用日线/收盘数据（避免 Agent 在盘后/盘前误报"数据失效"）。
    """
    from invest.config import get_settings
    from invest.intraday import _in_trading_window

    if not _in_trading_window():
        return {
            "ok": True,
            "stale": 0,
            "note": "非交易时段（休市），不检查实时行情；请使用日线/收盘数据（daily_bars）分析，"
                    "不要以实时价做盘中结论。",
        }
    from invest.data.realtime import realtime_health

    return realtime_health(get_settings().db_path)


def query_data_freshness(conn) -> dict:
    """数据新鲜度总览（2026-08-20）：daily_bars/index_bars/quant 最新时点 vs 最近交易日。

    回复涉及具体行情/板块/个股数据前先调用：fresh=False 时数据滞后，
    应说明"数据截至 XX"再回答，不要假装是当天最新。
    """
    import datetime as dt

    from invest.data.calendar import latest_trading_day

    exp = latest_trading_day(dt.date.today()).isoformat()
    latest_bars = conn.execute("SELECT MAX(date) FROM daily_bars").fetchone()[0] or ""
    latest_idx = conn.execute("SELECT MAX(date) FROM index_bars").fetchone()[0] or ""
    latest_q = conn.execute("SELECT MAX(run_date) FROM quant_strength").fetchone()[0] or ""
    # 2026-08-24：quant 是衍生指标（晚间/盘后计算），滞后不阻塞行情回答——只提示，不进 stale
    stale = [k for k, v in (("daily_bars", latest_bars), ("index_bars", latest_idx)) if v < exp]
    latest_all = max(latest_bars, latest_idx, latest_q) or "无"
    quant_stale = bool(latest_q and latest_q < exp)
    return {
        "expected_trading_day": exp,
        "daily_bars_latest": latest_bars or "无",
        "index_bars_latest": latest_idx or "无",
        "quant_latest": latest_q or "无",
        "fresh": not stale,
        "stale_parts": stale,
        "quant_stale": quant_stale,
        "note": ("数据已是最新" if not stale
                 else f"数据滞后：{','.join(stale)}（最新到 {latest_all}）；当日日线/板块数据源通常晚间才发布")
                + ("；quant 衍生指标滞后（盘后计算，正常现象），不阻塞行情回答" if quant_stale and not stale else ""),
    }


def web_search(query: str, n: int = 5) -> list[dict] | dict:
    """联网搜索（2026-08-21）：必应检索最新资讯/财报/新闻。返回 [{title,url,snippet}]。"""
    from invest.agent.web_tools import web_search as _ws

    return _ws(query, n=n)


def web_fetch(url: str) -> dict:
    """抓取网页正文（2026-08-21）。返回 {url, text}；WAF 保护页面（雪球）返回明确错误。"""
    from invest.agent.web_tools import web_fetch as _wf

    return _wf(url)


def xueqiu_search(keyword: str, n: int = 5) -> list[dict] | dict:
    """搜索雪球文章/大V（2026-08-24）：site:xueqiu.com 搜索摘要（雪球站内 WAF 抓不了正文，
    但必应索引了标题/摘要/URL）。返回 [{title, url, snippet}]。"""
    from invest.agent.web_tools import xueqiu_search as _xs

    return _xs(keyword, n=n)


# ---------- 雪球大V（2026-08-23 · big-v-monitor skill） ----------

def big_v_update(conn, action: str = "upsert_profile", profile_id: str = "", name: str = "",
                 platform: str = "xueqiu", xueqiu_id: str = "", homepage: str = "",
                 style: str = "", strengths: str = "", win_rate: str = "",
                 track_record: str = "", source_links: str = "", notes: str = "",
                 opinion_date: str = "", symbol: str = "", topic: str = "",
                 view: str = "", bias: str = "", confidence: float | None = None,
                 url: str = "") -> dict:
    """写入/更新雪球大V画像或观点（big_v_update）。

    - action=upsert_profile：按 id 更新画像（name 必填；profile_id 缺省时由 name 自动生成）；
    - action=upsert_opinion：追加一条观点（profile_id/view 必填，profile 须先存在）。
    返回 {ok, profile_id?, opinion_id?, error?}。
    """
    import datetime as dt
    import re

    import pandas as pd

    from invest.data.storage import upsert_df

    def _s(v: str) -> str | None:
        return (v or "").strip() or None

    if action == "upsert_profile":
        pid = _s(profile_id) or ("xq_" + re.sub(r"\s+", "_", (name or "").strip()))
        nm = _s(name)
        if not (pid and nm):
            return {"ok": False, "error": "upsert_profile 需 name（profile_id 缺省时自动生成）"}
        df = pd.DataFrame([{
            "id": pid, "name": nm, "platform": _s(platform) or "xueqiu",
            "xueqiu_id": _s(xueqiu_id), "homepage": _s(homepage), "style": _s(style),
            "strengths": _s(strengths), "win_rate": _s(win_rate), "track_record": _s(track_record),
            "source_links": _s(source_links), "notes": _s(notes),
            "updated_at": dt.date.today().isoformat(),
        }])
        upsert_df(conn, "big_v_profile", df)
        return {"ok": True, "profile_id": pid}
    if action == "upsert_opinion":
        pid = _s(profile_id)
        if not pid:
            return {"ok": False, "error": "upsert_opinion 需 profile_id（先 upsert_profile 建画像）"}
        exist = conn.execute("SELECT 1 FROM big_v_profile WHERE id=?", (pid,)).fetchone()
        if not exist:
            return {"ok": False, "error": f"画像不存在: {pid}，请先 upsert_profile"}
        vw = _s(view)
        if not vw:
            return {"ok": False, "error": "upsert_opinion 需 view（观点内容）"}
        cur = conn.execute(
            """INSERT INTO big_v_opinion(profile_id, opinion_date, symbol, topic, view, bias, confidence, url)
               VALUES(?,?,?,?,?,?,?,?)""",
            (pid, _s(opinion_date) or dt.date.today().isoformat(), _s(symbol), _s(topic), vw,
             _s(bias), confidence, _s(url)),
        )
        conn.commit()
        return {"ok": True, "profile_id": pid, "opinion_id": cur.lastrowid}
    return {"ok": False, "error": f"未知 action: {action}"}


def query_big_v(conn, name: str = "", profile_id: str = "", limit: int = 5) -> dict:
    """查询雪球大V画像与最近观点（big_v_monitor）。

    按 name（模糊）或 profile_id（精确）查；都不传则列出最近更新的画像。
    返回 {profiles: [...], opinions: [...]}。
    """
    limit = max(1, min(int(limit or 5), 20))
    cond, args = "1=1", []
    if profile_id:
        cond, args = "id=?", [profile_id]
    elif name:
        cond, args = "name LIKE ?", [f"%{name}%"]
    rows = _query(
        conn,
        f"SELECT * FROM big_v_profile WHERE {cond} ORDER BY updated_at DESC LIMIT ?",
        (*args, limit),
    )
    out = {"profiles": rows, "opinions": []}
    if rows:
        pids = [r["id"] for r in rows]
        marks = ",".join("?" * len(pids))
        out["opinions"] = _query(
            conn,
            f"""SELECT * FROM big_v_opinion WHERE profile_id IN ({marks})
                ORDER BY opinion_date DESC, id DESC LIMIT ?""",
            (*pids, limit * 5),
        )
    return out


# ---------- 报告小节 skill 复用（2026-08-23 · 日常对话按语义调 D 组现成分析） ----------

def _db_path_of(conn) -> str:
    """从连接拿数据库文件路径（PRAGMA database_list）。失败返回空串。"""
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        return str(row[2]) if row and row[2] else ""
    except Exception:
        return ""


def run_section(conn, section_id: str, n: int | None = None,
                period: str | None = None, top: int | None = None,
                symbols: list | None = None, **extra) -> dict:
    """运行报告 D 组小节 skill（2026-08-23）：日常对话复用现成分析函数。

    按 section_id 调 invest.skills.runner.run（含参数校验），返回 {section, text}。
    db_path 自动从连接注入（小节声明 db_path 才传）；extra 透传给小节（如 d8 的 score）。
    失败返回 {error} 不抛异常。
    """
    from invest.skills.registry import get
    from invest.skills.runner import run as _run_section

    try:
        mod = get(section_id)
    except KeyError:
        return {"error": f"未知小节 skill: {section_id}"}
    # 2026-08-25：仅行情依赖小节过新鲜度守卫；消息/舆情/宏观/观点/评级/外围等跳过
    if _freshness_gate_on() and section_id not in _SECTION_NO_GATE:
        ok, reason = freshness_gate(conn)
        if not ok:
            return {"error": reason}
    spec = mod.SKILL.get("params") or {}
    kwargs: dict = {}
    if "db_path" in spec:
        db_path = _db_path_of(conn)
        if not db_path:
            return {"error": "无法定位数据库路径"}
        kwargs["db_path"] = db_path
    if n is not None:
        kwargs["n"] = n
    if period is not None:
        kwargs["period"] = period
    if top is not None:
        kwargs["top"] = top
    if symbols is not None:
        kwargs["symbols"] = symbols
    kwargs.update(extra)
    try:
        text = _run_section(section_id, **kwargs)
    except TypeError as exc:
        return {"error": f"参数错误: {exc}"}
    except Exception as exc:
        return {"error": f"小节运行失败: {type(exc).__name__}: {exc}"}
    return {"section": section_id, "text": text}


def run_skill(symbol: str, depth: str = "lite") -> dict:
    """跑 UZI 深度分析流水线（2026-08-21）：多维数据→LLM 多轮→HTML 报告。
    返回 {ok, report_path, summary}；depth: lite(快)/medium/deep。

    2026-08-21 异步化：注册了 run_skill sink（飞书场景）时，改为后台线程执行，
    立即返回"已启动"提示（不阻塞对话线程），完成后由 sink 把结果发回原会话；
    未注册 sink（脚本/测试）时保持同步原逻辑。

    2026-08-23 门禁：**仅在用户明确提到『UZI』时运行**。thread-local 记录最近用户
    原文（set_current_user_text），不含 "uzi"（大小写不敏感）时拒绝——'深度分析/
    完整报告'等词不代表要跑 UZI；未设置 user_text 的脚本/测试场景不拦截。
    """
    user_text = (getattr(_thread_chat, "user_text", "") or "")
    if user_text.strip() and "uzi" not in user_text.lower():
        return {
            "ok": False,
            "error": "UZI 深度分析仅在用户明确提到『UZI』时运行（如『跑 UZI 分析 600519』）。"
                     "当前问题不含 UZI，请改用角度分析或 query_stock_daily/cross_validate；"
                     "若确实要跑 UZI，请明确说出 UZI。",
        }
    if _run_skill_sink is None:
        from invest.agent.skill_runner import run_skill as _rs

        return _rs(symbol, depth=depth)
    # 异步：后台线程执行，完成后回调 sink(结果, chat_id)
    import threading

    chat_id = getattr(_thread_chat, "chat_id", "")
    depth = depth if depth in ("lite", "medium", "deep") else "lite"
    est = {"lite": "约5-10分钟", "medium": "约10-15分钟", "deep": "约15-20分钟"}[depth]

    def _worker() -> None:
        from invest.agent.skill_runner import run_skill as _rs

        try:
            result = _rs(symbol, depth=depth)
        except Exception as exc:
            result = {"ok": False, "error": f"深度分析异常: {type(exc).__name__}: {exc}"}
        try:
            _run_skill_sink(result, chat_id)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("run_skill 结果回调失败: %s", exc)

    threading.Thread(target=_worker, daemon=True, name="run-skill").start()
    return {
        "ok": True,
        "async": True,
        "note": f"深度分析已启动（{depth} 档，{est}），完成后会自动把报告摘要与路径发给你。",
    }


# ---------- load_skill：方法论按需加载（2026-08-27 · 全能 Agent） ----------

# 别名归一：用户/模型叫法 → skill 目录名（缺省按原名找）
_SKILL_ALIASES = {
    "debug": "systemdebugging",
    "diagnosing": "systemdebugging",
    "diagnosing-bugs": "systemdebugging",
    "诊断": "systemdebugging",
    "调试": "systemdebugging",
    "grill": "grill-me",
    "grillme": "grill-me",
    "拷问": "grill-me",
    "brainstorm": "brainstorming",
    "头脑风暴": "brainstorming",
    "start-here": "00-start-here",
    "入门": "00-start-here",
}


def load_skill(name: str) -> dict:
    """加载指定方法论 skill 的完整指令（SKILL.md 全文），本次回答按其执行。

    命中触发词时调用：grill-me/grilling（grill/拷问/挑战想法/找漏洞）、brainstorming
    （头脑风暴/设计/方案/怎么做）、systemdebugging（debug/诊断/排查/为什么坏了/变慢）、
    角度 skill（stock-emotion/technical/fundamental/cycle、trap-scan、sector-analysis、
    opinion-analysis、big-v-monitor）、A-Stock-Skills/start-here 等。
    返回 {skill, text}；未知名返回 {error, available} 附可用清单，可换名重试。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    key = (name or "").strip().lower().replace(" ", "-")
    key = _SKILL_ALIASES.get(key, key)
    for base in (root / ".dsh" / "skills", root / ".claude" / "skills"):
        f = base / key / "SKILL.md"
        if f.exists():
            try:
                text = f.read_text(encoding="utf-8")[:12000]
            except Exception as exc:
                return {"error": f"读取 {key} 失败: {exc}"}
            return {"skill": key, "name": name, "text": text}
    available = sorted(
        d.name for base in (root / ".dsh" / "skills", root / ".claude" / "skills")
        if base.exists() for d in base.iterdir() if (d / "SKILL.md").exists()
    )
    return {"error": f"未找到 skill: {name}", "available": available}


# ---- run_skill 异步 sink（飞书场景注册，见 invest/push/feishu_ws.py） ----

_run_skill_sink = None  # callable(result: dict, chat_id: str) -> None
_thread_chat = threading.local()


def set_run_skill_sink(fn) -> None:
    """注册 run_skill 异步完成回调（feishu_ws 启动时调用）。fn(result, chat_id)。"""
    global _run_skill_sink
    _run_skill_sink = fn


def set_current_chat(chat_id: str) -> None:
    """记录当前处理消息的会话 id（feishu_ws 事件线程内调用，thread-local）。"""
    _thread_chat.chat_id = chat_id


def set_current_user_text(text: str) -> None:
    """记录当前处理消息的用户原文（thread-local，供 run_skill 门禁判断是否明确提到 UZI）。"""
    _thread_chat.user_text = text or ""


def set_freshness_gate(enabled: bool) -> None:
    """开关数据新鲜度硬门禁（thread-local，2026-08-23）。

    飞书对话（feishu_ws._agent_chat）启用：数据工具执行前强制检查新鲜度，
    数据滞后时返回原因而非数据，模型拿不到旧数据就无法硬给结论。
    脚本/测试默认关闭，不影响现有行为。
    """
    _thread_chat.freshness_gate = bool(enabled)


def _freshness_gate_on() -> bool:
    return bool(getattr(_thread_chat, "freshness_gate", False))


# 数据类工具（执行前需过新鲜度门禁，2026-08-25 收窄：只留个股/行业行情核心）
# query_temperature(涨停池)/query_strength(quant)/query_macro(宏观)/query_pool(候选池)等
# 数据源为独立采集表或低频/quant 衍生（quant_stale 不阻塞原则）——不受 daily_bars 守卫约束
_DATA_TOOLS = {
    "query_stock_daily",
    "cross_validate",
}

# run_section 内：**不依赖本地行情新鲜度**的小节——数据源为联网（财联社电报/社区搜索）、
# 低频独立表（宏观/评级/候选池/外围快照/涨停池/资金流/龙虎榜/板块）或 quant 衍生，
# 跳过 freshness_gate（2026-08-25 全面排查：仅直接读 daily_bars 的小节保留守卫）
_SECTION_NO_GATE = {
    "d1_news_block", "d2_focus_industries", "d3_style", "d4_strength", "d5_movers",
    "d6_macro", "d7_agent_viewpoints", "d8_temp_guide", "d9_rating_guide",
    "d10_action_guide", "d11_emotion", "d12_limit_up_ladder", "d13_fund_line",
    "d14_sector_moves", "d15_capital_leaders", "d17_pool_delta", "d20_entry_timing",
    "d21_freshness", "d22_ratings", "d23_breadth", "d24_global_snapshot",
    "d25_overnight_analysis", "d26_market_watch", "d27_news_digest",
    "d28_community_hot", "d29_sector_resonance", "d30_cycle_position",
}
# 保留守卫（直接读 daily_bars 行情）：d16 持仓警戒收盘价 / d18 异常波动 / d19 做T / d31 杀猪盘K线

_fresh_cache: dict[str, tuple[float, tuple[bool, str]]] = {}
_FRESH_TTL = 30.0  # 新鲜度判定缓存 30s（避免每次工具调用都查库）


def freshness_gate(conn) -> tuple[bool, str]:
    """数据新鲜度门禁（2026-08-23 对话守卫）：返回 (可答, 说明)。

    - 交易时段：实时行情健康才可答（当日日线晚间才发布属正常，不看日线）；
    - 非交易时段：daily_bars/index_bars 到最近交易日才可答，滞后返回原因。
    带 30s 缓存；判定失败保守放行（不误伤）。
    """
    import datetime as _dt
    import time as _time

    from invest.data.calendar import latest_trading_day

    db = _db_path_of(conn) or "memory"
    now = _time.time()
    cached = _fresh_cache.get(db)
    if cached and now - cached[0] < _FRESH_TTL:
        return cached[1]

    def _set(out: tuple[bool, str]) -> tuple[bool, str]:
        _fresh_cache[db] = (now, out)
        return out

    exp = latest_trading_day(_dt.date.today()).isoformat()
    # 交易时段：实时行情健康优先
    try:
        from invest.intraday import _in_trading_window

        if _in_trading_window():
            from invest.config import get_settings
            from invest.data.realtime import realtime_health

            rh = realtime_health(get_settings().db_path)
            if not rh.get("ok", False):
                detail = rh.get("last_detail") or f"stale={rh.get('stale', 0)}"
                return _set((False, f"实时行情数据失效/过期（{detail}），暂不提供数据与结论，请稍后重试"))
            return _set((True, ""))
    except Exception:
        pass
    # 非交易时段：日线/指数**任一**到最近交易日即可放行（2026-08-24 放宽——
    # 盘前/休市问最近交易日数据属正常，个股或指数任一方有当日快照即可答；两者都缺才拦截）
    try:
        latest_bars = conn.execute("SELECT MAX(date) FROM daily_bars").fetchone()[0] or ""
        latest_idx = conn.execute("SELECT MAX(date) FROM index_bars").fetchone()[0] or ""
    except Exception:
        return _set((True, ""))
    stale = [k for k, v in (("日线", latest_bars), ("指数", latest_idx)) if v < exp]
    if len(stale) == 2:
        latest = max(latest_bars, latest_idx) or "无"
        return _set((False, (f"数据截至 {latest}（日线/指数均未到最近交易日 {exp}），"
                             f"数据滞后暂不提供数据与结论，请稍后重试")))
    return _set((True, ""))


def cross_validate(conn, obj: str, obj_type: str = "industry") -> dict:
    """多源交叉验证（A-Stock-Skills 多源校验思想，2026-08-16）。

    对 obj（行业名或个股代码）一次性汇总四个独立维度的最新信号：
    - strength: 相对强度 RS / 趋势阶段 / 动量（短线轨）；
    - capital:   资金属性 / 风格标签；
    - linkage:   高相关联动板块（相关度≥0.7）；
    - valuation: 估值分位（PE/PB）与拥挤度（行业）；
    返回 {obj, dimensions: {...}, n_dimensions, summary}。
    """
    out: dict = {}
    if obj_type == "stock":
        # 个股：强度 + 资金 + 行业维度（不覆盖个股自身维度）
        try:
            rows = _query(
                conn,
                """SELECT obj, rs, trend_stage FROM quant_strength
                   WHERE obj_type='stock' AND period='short' AND obj=?
                     AND run_date = (SELECT MAX(run_date) FROM quant_strength
                                     WHERE obj_type='stock' AND period='short' AND obj=?)""",
                (obj, obj),
            )
            out["strength"] = rows[0] if rows else None
        except Exception:
            out["strength"] = None
        try:
            rows = _query(
                conn,
                """SELECT fund_type, style, confidence FROM quant_capital
                   WHERE obj_type='stock' AND obj=?
                     AND run_date = (SELECT MAX(run_date) FROM quant_capital
                                     WHERE obj_type='stock' AND obj=?)""",
                (obj, obj),
            )
            out["capital"] = rows[0] if rows else None
        except Exception:
            out["capital"] = None
        # 个股→行业（手工映射）→ 行业维度放进 industry 子键，不覆盖个股维度
        try:
            from invest.data.industry_map import industry_of
            ind = industry_of(conn, obj)
            if ind:
                out["industry"] = {"name": ind, **_industry_dimensions(conn, ind)}
        except Exception:
            pass
    else:
        out.update(_industry_dimensions(conn, obj))
    n = sum(1 for v in out.values() if v is not None and v != {})
    return {"obj": obj, "obj_type": obj_type, "dimensions": out, "n_dimensions": n}


def _industry_dimensions(conn, industry: str) -> dict:
    """行业四维度：强度/资金/联动/估值。"""
    res: dict = {}
    try:
        rows = _query(
            conn,
            """SELECT obj, rs, rs5, rs10, rs20, momentum, trend_stage FROM quant_strength
               WHERE obj_type='industry' AND period='short' AND obj=?
                 AND run_date = (SELECT MAX(run_date) FROM quant_strength
                                 WHERE obj_type='industry' AND period='short')""",
            (industry,),
        )
        res["strength"] = rows[0] if rows else None
    except Exception:
        res["strength"] = None
    try:
        rows = _query(
            conn,
            """SELECT fund_type, style, confidence FROM quant_capital
               WHERE obj_type='industry' AND obj=?
                 AND run_date = (SELECT MAX(run_date) FROM quant_capital
                                 WHERE obj_type='industry' AND obj=?)""",
            (industry, industry),
        )
        res["capital"] = rows[0] if rows else None
    except Exception:
        res["capital"] = None
    try:
        rows = _query(
            conn,
            """SELECT a, b, corr, lead FROM quant_linkage
               WHERE run_date = (SELECT MAX(run_date) FROM quant_linkage)
                 AND (a=? OR b=?) AND corr>=0.7 ORDER BY corr DESC LIMIT 5""",
            (industry, industry),
        )
        res["linkage"] = rows
    except Exception:
        res["linkage"] = []
    try:
        rows = _query(
            conn,
            """SELECT pe_pct, pb_pct, crowding, crowding_state FROM quant_valuation
               WHERE obj=?
                 AND run_date = (SELECT MAX(run_date) FROM quant_valuation WHERE obj=?)""",
            (industry, industry),
        )
        res["valuation"] = rows[0] if rows else None
    except Exception:
        res["valuation"] = None
    return res


def send_direction_hint(conn, direction: str, obj: str, reason: str) -> dict:
    tid = ticket_mod.create_ticket(
        conn, "direction_hint", from_agent="research", to_agent="trade",
        direction=direction, payload={"obj": obj, "reason": reason},
    )
    return {"ticket_id": tid}


def request_attribution(conn, obj: str, reason: str) -> dict:
    tid = ticket_mod.create_ticket(
        conn, "attribution_request", from_agent="trade", to_agent="research",
        payload={"obj": obj, "reason": reason},
    )
    return {"ticket_id": tid}


TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "query_strength", "description": "查询相对强度榜（短线/中线轨；默认行业）", "parameters": {"type": "object", "properties": {"period": {"type": "string", "enum": ["short", "mid"]}, "top": {"type": "integer"}, "obj_type": {"type": "string", "enum": ["industry", "stock"]}}, "required": []}}},
    {"type": "function", "function": {"name": "query_rotation", "description": "查询板块轮动排名与领涨滞后", "parameters": {"type": "object", "properties": {"top": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "query_temperature", "description": "查询市场温度", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "query_capital", "description": "查询行业资金属性与风格", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "query_linkage", "description": "查询行业高相关联动对", "parameters": {"type": "object", "properties": {"threshold": {"type": "number"}, "top": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "query_macro", "description": "查询宏观流动性加工指标", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "query_pool", "description": "查询候选池与关注度", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "write_viewpoint", "description": "写入结构化观点（必须含五要素）", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "conclusion": {"type": "string"}, "period_tag": {"type": "string", "enum": ["micro", "short", "mid", "long"]}, "confidence": {"type": "number"}, "evidence": {"type": "array", "items": {"type": "object"}}, "invalid_condition": {"type": "string"}, "obj_type": {"type": "string"}, "obj": {"type": "string"}}, "required": ["source", "conclusion", "period_tag", "confidence", "evidence", "invalid_condition"]}}},
    {"type": "function", "function": {"name": "send_direction_hint", "description": "投研→交易：方向提示单", "parameters": {"type": "object", "properties": {"direction": {"type": "string"}, "obj": {"type": "string"}, "reason": {"type": "string"}}, "required": ["direction", "obj", "reason"]}}},
    {"type": "function", "function": {"name": "query_realtime_health", "description": "查询实时行情数据健康状态；ok=false 表示行情失效/过期，此时禁止基于实时价格给出开仓或止损决策", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "request_attribution", "description": "交易→投研：归因请求单", "parameters": {"type": "object", "properties": {"obj": {"type": "string"}, "reason": {"type": "string"}}, "required": ["obj", "reason"]}}},
    {"type": "function", "function": {"name": "cross_validate", "description": "多源交叉验证：对某行业或个股汇总四维度最新信号（强度RS/趋势 / 资金风格 / 高相关联动 / 估值PE/PB分位与拥挤度），用于确认方向是否多维度共振", "parameters": {"type": "object", "properties": {"obj": {"type": "string"}, "obj_type": {"type": "string", "enum": ["industry", "stock"], "description": "默认 industry"}}, "required": ["obj"]}}},
    {"type": "function", "function": {"name": "query_stock_daily", "description": "查询任意个股日线数据（最近收盘价/1/5/20日涨跌幅/窗口内高低/最近5条K线）。本地有历史时用三源实时接口拼当日（收盘后=收盘价，不等晚间历史接口）；本地完全无该股时按需联网拉取（akshare 东财→新浪）。周期决定 days：短线/游资视角=60；中线=250；长线=500。分析个股时优先用这个工具拿收盘数据，再配 cross_validate 看多维度；**查实时价/涨跌幅用 query_realtime_quote**", "parameters": {"type": "object", "properties": {"symbol": {"type": "string", "description": "6位股票代码，如 600519"}, "days": {"type": "integer", "description": "交易日数：短线60/中线250/长线500，默认60"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "query_realtime_quote", "description": "实时报价（2026-08-25）：个股/指数/ETF 三源实时快照——盘中=现价、收盘后=收盘价。查'XX 现在多少/实时/今天涨跌'用这个，不受日线新鲜度守卫约束（实时即最新）", "parameters": {"type": "object", "properties": {"symbol": {"type": "string", "description": "个股代码（obj_type=stock 时）"}, "obj_type": {"type": "string", "enum": ["stock", "index", "etf"], "description": "默认 stock"}, "symbols": {"type": "array", "items": {"type": "string"}, "description": "批量代码（obj_type=etf 时可选）"}}, "required": []}}},
    {"type": "function", "function": {"name": "query_lhb", "description": "查询个股龙虎榜（2026-08-25）：本地 dragon_tiger 表按股票代码/名称查最近 N 次（日期/买入/卖出/净额）。问'XX 的龙虎榜/谁在买卖 XX'用这个（本地数据已采集）；查最新榜单 TOP 用 run_section d15", "parameters": {"type": "object", "properties": {"symbol": {"type": "string", "description": "6位股票代码，如 002083"}, "name": {"type": "string", "description": "股票名称（symbol 缺失时用）"}, "n": {"type": "integer", "description": "最近 N 次，默认5"}}, "required": []}}},
    {"type": "function", "function": {"name": "xueqiu_fetch_user", "description": "抓雪球用户主页动态（2026-08-25，Playwright 真实浏览器过 WAF）：返回该大V 最近文章列表（标题+URL），并自动建/更新 big_v_profile 画像。问'某雪球大V 最近发了什么'用这个；拿到文章 URL 后再用 xueqiu_fetch_article 抓正文", "parameters": {"type": "object", "properties": {"user_id": {"type": "string", "description": "雪球用户 ID（数字）或主页 URL，如 6192813830"}, "limit": {"type": "integer", "description": "动态条数，默认10"}}, "required": ["user_id"]}}},
    {"type": "function", "function": {"name": "xueqiu_fetch_article", "description": "抓雪球文章正文（2026-08-25，Playwright 过 WAF）：返回标题/时间/作者/正文，并按 url 去重写入 big_v_opinion（需先 xueqiu_fetch_user 建画像）。搜到/拿到雪球文章链接后要读全文用这个（web_fetch 抓不了雪球，WAF 保护）", "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "雪球文章链接，如 https://xueqiu.com/6192813830/366009201"}, "profile_id": {"type": "string", "description": "大V 画像 id（xq_xxx，可选；提供则入库）"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "query_data_freshness", "description": "数据新鲜度总览：daily_bars/index_bars/quant 最新时点 vs 最近交易日。回答涉及具体行情/板块/个股数据的问题前必须先调用；fresh=false 时说明数据截至时间再回答", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "web_search", "description": "联网搜索（必应）：查最新资讯/财报/公告/新闻/政策。系统本地数据查不到的最新信息用这个", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}, "n": {"type": "integer", "description": "结果条数，默认5"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "web_fetch", "description": "抓取指定网页正文（用于看搜索到的链接详情）", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "xueqiu_search", "description": "搜索雪球文章/大V（2026-08-24）：site:xueqiu.com 返回标题+摘要+链接（雪球站内被 WAF 保护无法抓正文，用搜索摘要获取某大V 近期文章/热门讨论）", "parameters": {"type": "object", "properties": {"keyword": {"type": "string", "description": "大V名/股票/关键词"}, "n": {"type": "integer", "description": "条数，默认5"}}, "required": ["keyword"]}}},
    {"type": "function", "function": {"name": "run_skill", "description": "跑 UZI 深度分析流水线（多维数据→LLM 多轮→HTML 报告）。**仅在用户明确提到『UZI』时调用**（如'跑 UZI'）；'深度分析/完整报告/全面分析'等词不代表要跑 UZI，改用 query_stock_daily/cross_validate 或角度 skill；depth=lite 快（约1分钟）", "parameters": {"type": "object", "properties": {"symbol": {"type": "string", "description": "6位股票代码或名称"}, "depth": {"type": "string", "enum": ["lite", "medium", "deep"]}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "big_v_update", "description": "写入/更新雪球大V画像或观点（big-v-monitor skill 用）。action=upsert_profile 更新画像（name 必填，profile_id 缺省自动生成）；action=upsert_opinion 追加观点（profile_id/view 必填，画像须先存在）。搜索到大V 新资料后调用沉淀，供下次复用", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["upsert_profile", "upsert_opinion"]}, "profile_id": {"type": "string"}, "name": {"type": "string"}, "platform": {"type": "string"}, "xueqiu_id": {"type": "string"}, "homepage": {"type": "string"}, "style": {"type": "string", "description": "风格：价投/成长/游资/宏观/量化/技术/趋势"}, "strengths": {"type": "string", "description": "擅长方向"}, "win_rate": {"type": "string", "description": "自述/公开胜率（注明口径）"}, "track_record": {"type": "string"}, "source_links": {"type": "string"}, "notes": {"type": "string"}, "opinion_date": {"type": "string", "description": "观点发表日 YYYY-MM-DD，缺省今天"}, "symbol": {"type": "string"}, "topic": {"type": "string"}, "view": {"type": "string", "description": "观点内容"}, "bias": {"type": "string", "enum": ["bullish", "bearish", "neutral"]}, "confidence": {"type": "number", "description": "0-1，可空"}, "url": {"type": "string", "description": "原文链接"}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "query_big_v", "description": "查询雪球大V画像与最近观点（big-v-monitor skill 用）。按 name 模糊或 profile_id 精确查；都不传则列出最近更新的画像。返回 profiles + opinions（含观点日期/标的/多空倾向/链接）", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "profile_id": {"type": "string"}, "limit": {"type": "integer", "description": "画像条数，默认5"}}, "required": []}}},
    {"type": "function", "function": {"name": "run_section", "description": "运行报告 D 组小节 skill（2026-08-23：日常对话按语义调用现成分析文本，返回 {section,text}）。section_id 全量：d1_news_block 消息面提炼(财联社电报→LLM挑重点)｜d2_focus_industries 重点关注行业｜d3_style 市场风格(大小盘/题材)｜d4_strength 行业强度榜(period/top)｜d5_movers 涨跌榜(n)｜d6_macro 宏观流动性｜d7_agent_viewpoints Agent观点(n)｜d8_temp_guide 温度倾向(score)｜d9_rating_guide 评级·仓位指南｜d10_action_guide 仓位建议(score)｜d11_emotion 情绪人气(涨停/连板/炸板+情绪周期)｜d12_limit_up_ladder 连板梯队｜d13_fund_line 资金主线(n)｜d14_sector_moves 板块异动(n)｜d15_capital_leaders 龙虎榜龙头(n)｜d16_card_alerts 持仓警戒｜d17_pool_delta 候选池变化｜d18_abnormal_moves 异常波动(n)｜d20_entry_timing 建仓时机｜d22_ratings 宏观/市场评级｜d23_breadth 涨跌家数｜d24_global_snapshot 隔夜外围｜d25_overnight_analysis 外围影响解读(LLM)｜d26_market_watch 涨停异动监控(停牌/暴雷)｜d27_news_digest 消息汇总(宏观/个股/市场外+风险)｜d28_community_hot 社区热议(n)｜d29_sector_resonance 板块共振(n)｜d30_cycle_position 周期行业定位｜d31_pool_trap_alerts 候选池预警。不适用：d19_t_trade_hints 需实时价参数（对话不用）；d21_freshness 数据新鲜度用 query_data_freshness 工具。问'情绪/连板/板块/资金/宏观/外围/周期/消息/风格/持仓/异常'等现成统计或消息面时优先用这个，比裸查表更完整", "parameters": {"type": "object", "properties": {"section_id": {"type": "string", "description": "D 组小节 id（见描述清单）"}, "n": {"type": "integer", "description": "TOP n / 消息条数，多数小节可选，默认3-5"}, "period": {"type": "string", "description": "d4_strength 用：short/mid"}, "top": {"type": "integer", "description": "d4_strength 用：条数"}, "symbols": {"type": "array", "items": {"type": "string"}, "description": "d31 用：指定扫描标的"}}, "required": ["section_id"]}}},
    {"type": "function", "function": {"name": "load_skill", "description": "加载指定方法论 skill 的完整指令（SKILL.md 全文），本次回答按其执行。命中触发词时调用：grill-me/grilling（grill/拷问/挑战我的想法/找漏洞/拷问式需求对齐）、brainstorming（头脑风暴/设计/方案/怎么实现）、systemdebugging（debug/诊断/排查/为什么坏了/性能变慢/系统化排障）、角度 skill（stock-emotion/technical/fundamental/cycle、trap-scan、sector-analysis、opinion-analysis、big-v-monitor）、A-Stock-Skills/start-here 等。返回 {skill, text}；未知名返回 error 附可用清单，换名重试", "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "skill 目录名或常用叫法（如 grill-me / brainstorming / systemdebugging / stock-fundamental / debug / grill）"}}, "required": ["name"]}}},
]

_IMPLEMENTATIONS = {
    "query_strength": query_strength,
    "query_rotation": query_rotation,
    "query_temperature": query_temperature,
    "query_capital": query_capital,
    "query_linkage": query_linkage,
    "query_macro": query_macro,
    "query_pool": query_pool,
    "write_viewpoint": write_viewpoint,
    "send_direction_hint": send_direction_hint,
    "request_attribution": request_attribution,
    "query_realtime_health": query_realtime_health,
    "cross_validate": cross_validate,
    "query_stock_daily": query_stock_daily,
    "query_realtime_quote": query_realtime_quote,
    "query_lhb": query_lhb,
    "xueqiu_fetch_user": xueqiu_fetch_user,
    "xueqiu_fetch_article": xueqiu_fetch_article,
    "query_data_freshness": query_data_freshness,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "xueqiu_search": xueqiu_search,
    "run_skill": run_skill,
    "big_v_update": big_v_update,
    "query_big_v": query_big_v,
    "run_section": run_section,
    "load_skill": load_skill,
}


def build_dispatch(conn: sqlite3.Connection, source: str = "research") -> dict:
    """把工具绑定到指定数据库连接；写观点工具来源由服务端固定注入。

    LLM 即使传 source 参数也会被覆盖，避免观点来源绕过仲裁与准确率统计。

    2026-08-23：数据类工具（_DATA_TOOLS）挂新鲜度硬门禁——set_freshness_gate(True)
    时（飞书对话），执行前先过 freshness_gate，数据滞后直接返回原因而非数据。
    """
    # 非 DB 工具（联网/技能）第一个参数不是 conn，不能 partial(conn)
    no_conn = {"web_search", "web_fetch", "run_skill", "load_skill", "xueqiu_search"}
    out: dict = {}
    for name, fn in _IMPLEMENTATIONS.items():
        bound = fn if name in no_conn else functools.partial(fn, conn)
        if name in _DATA_TOOLS:

            def _guarded(*a, _fn=bound, **k):
                if _freshness_gate_on():
                    ok, reason = freshness_gate(conn)
                    if not ok:
                        return {"error": reason}
                return _fn(*a, **k)

            out[name] = _guarded
        else:
            out[name] = bound

    def _write(**kwargs):
        kwargs.pop("source", None)
        return write_viewpoint(conn, source=source, **kwargs)

    out["write_viewpoint"] = _write
    return out