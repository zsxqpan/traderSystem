"""行业/个股事实卡：规则填维度，AI 只提炼带来源的近 3–7 日事实。"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from invest.db import connect

logger = logging.getLogger(__name__)

RULE_VERSION = "factcard-v1"
DIMENSIONS = (
    "strength",
    "rotation",
    "valuation",
    "crowding",
    "capital",
    "cycle",
    "macro",
)
MAX_DEEP_INDUSTRIES = 5
MAX_DEEP_STOCKS = 20
NEWS_LOOKBACK_DAYS = 7
FORBIDDEN_RANK_KEYS = {
    "buy_rank",
    "sell_rank",
    "trade_rank",
    "score_rank",
    "综合排名",
    "买卖排名",
    "ranking",
}
RS_CHANGE = 0.05
PE_CHANGE = 0.10
RANK_CHANGE = 3
NewsFn = Callable[[str, str, str], list[dict]]


@dataclass
class EvidenceItem:
    evidence_id: str
    kind: str
    source: str
    as_of: str
    summary: str
    url: str = ""
    published_at: str = ""
    fetched_at: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class FactCard:
    obj_type: str
    obj: str
    as_of: str
    data_version: str
    rule_version: str
    dimensions: dict[str, Any]
    missing: list[str]
    evidence: list[EvidenceItem] = field(default_factory=list)
    card_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return _strip_rank_keys(payload)


def _strip_rank_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_rank_keys(v)
            for k, v in value.items()
            if k not in FORBIDDEN_RANK_KEYS
        }
    if isinstance(value, list):
        return [_strip_rank_keys(v) for v in value]
    return value


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _row(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    found = conn.execute(sql, params).fetchone()
    return dict(found) if found else None


def _as_of_max(
    conn: sqlite3.Connection,
    table: str,
    date_col: str,
    as_of: str,
    extra: str = "",
    extra_params: tuple = (),
) -> str | None:
    sql = f"SELECT MAX({date_col}) AS d FROM {table} WHERE {date_col}<=?"
    params: tuple = (as_of,)
    if extra:
        sql += " AND " + extra
        params = (as_of, *extra_params)
    row = conn.execute(sql, params).fetchone()
    return row["d"] if row and row["d"] else None


def _data_version(conn: sqlite3.Connection, as_of: str) -> str:
    parts = [f"as_of={as_of}", f"rule={RULE_VERSION}"]
    for table, col in (
        ("quant_strength", "run_date"),
        ("quant_rotation", "run_date"),
        ("quant_valuation", "run_date"),
        ("quant_capital", "run_date"),
        ("sector_fund_flow", "date"),
        ("ratings", "date"),
    ):
        latest = _as_of_max(conn, table, col, as_of)
        if latest:
            parts.append(f"{table}={latest}")
    return "|".join(parts)


def _next_evidence_id(conn: sqlite3.Connection | None, as_of: str, seq: int) -> str:
    compact = as_of.replace("-", "")
    return f"EVID-{compact}-{seq:04d}"


def _evidence_seq_start(conn: sqlite3.Connection, as_of: str) -> int:
    compact = as_of.replace("-", "")
    prefix = f"EVID-{compact}-"
    row = conn.execute(
        """SELECT MAX(CAST(substr(evidence_id, length(?) + 1) AS INTEGER)) AS n
           FROM fact_evidence WHERE as_of=? AND evidence_id LIKE ?""",
        (prefix, as_of, prefix + "%"),
    ).fetchone()
    return int(row["n"] or 0) + 1


def _within_news_window(published_at: str, as_of: str) -> bool:
    if not published_at:
        return False
    try:
        pub = dt.date.fromisoformat(str(published_at)[:10])
        end = dt.date.fromisoformat(as_of)
    except ValueError:
        return False
    start = end - dt.timedelta(days=NEWS_LOOKBACK_DAYS)
    return start <= pub <= end


def _source_from_url(url: str) -> str:
    if not url:
        return ""
    from urllib.parse import urlparse

    host = (urlparse(url).netloc or "").lower()
    return host.removeprefix("www.")


def _parse_news_json(text: str) -> list[dict]:
    blob = (text or "").strip()
    if blob.startswith("```"):
        blob = blob.strip("`")
        if blob.lower().startswith("json"):
            blob = blob[4:]
        blob = blob.strip()
    start, end = blob.find("["), blob.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("LLM 提炼结果不是 JSON 数组")
    data = json.loads(blob[start : end + 1])
    if not isinstance(data, list):
        raise TypeError("LLM 提炼结果不是数组")
    return [row for row in data if isinstance(row, dict)]


def _llm_refine_news(obj: str, as_of: str, hits: list[dict]) -> list[dict]:
    from invest.agent.llm import LLMClient
    from invest.config import get_settings

    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM，无法提炼近3-7日消息")
    client = LLMClient(settings=settings)
    material = json.dumps(hits, ensure_ascii=False)[:6000]
    sys_prompt = (
        f"你是财经事实编辑。只根据素材提炼与「{obj}」相关、发布日在 {as_of} 往前 7 日内的"
        "消息/公告/舆情。每条必须带来源与 published_at（YYYY-MM-DD）。"
        "禁止编造来源或日期；没有发布日的不要输出；不要给出买卖排名或综合排名。"
        "只输出 JSON 数组，元素字段：kind(news|announcement|sentiment), source, url, published_at, summary。"
    )
    text = client.run(system=sys_prompt, user=material, job="factcard_news", max_turns=1)
    return _parse_news_json(text)


def _collect_web_hits(obj: str, as_of: str) -> list[dict]:
    from invest.agent.web_tools import web_search

    hits: list[dict] = []
    errors: list[str] = []
    for query in (f"{obj} 新闻 公告", f"{obj} 舆情 讨论"):
        try:
            raw = web_search(query, n=5)
        except Exception as exc:
            errors.append(str(exc))
            continue
        if isinstance(raw, dict) and raw.get("error"):
            errors.append(str(raw["error"]))
            continue
        if not isinstance(raw, list):
            continue
        for hit in raw:
            if not isinstance(hit, dict):
                continue
            url = str(hit.get("url") or "")
            # 只认引擎/页面级发布日，禁止用 snippet/title 前缀日期顶 published_at。
            published = str(
                hit.get("published_at") or hit.get("published") or hit.get("date") or ""
            ).strip()
            source = str(hit.get("source") or "").strip() or _source_from_url(url)
            if not source or not published:
                continue
            published = published[:10]
            if not _within_news_window(published, as_of):
                continue
            hits.append({
                "kind": "news",
                "source": source,
                "url": url,
                "published_at": published,
                "fetched_at": as_of,
                "summary": str(hit.get("snippet") or hit.get("title") or "")[:500],
                "title": hit.get("title") or "",
            })
    if not hits and errors:
        raise RuntimeError("；".join(errors))
    return hits


_telegraph_cache: dict[str, tuple[float, object]] = {}
_TELEGRAPH_TTL = 1800.0  # 电报快讯批次缓存 30 分钟（覆盖一次 factcard_refresh/deep_dive 全程）


def _telegraph_industry_facts(obj: str, as_of: str, max_items: int = 5) -> list[dict]:
    """电报快讯按主题关键词匹配（真实来源+发布时间，近 3-7 日窗口）。

    2026-08-31：web 检索源（DeepSeek 官方/必应等）返回的 hit 无发布日与来源字段，
    news 维度恒空；改用带时间戳的电报快讯。**优先东财全球财经快讯**
    （本机实测 200 条/日、行业覆盖 ~15/90），财联社电报兜底（实测仅 20 条/日、
    覆盖 ~2/90）。无匹配返回 []（调用方回退 web 检索）。
    """
    import time as _time

    now = _time.time()
    cached = _telegraph_cache.get(as_of)
    if cached and now - cached[0] < _TELEGRAPH_TTL:
        df, src = cached[1]
    else:
        import akshare as ak

        df, src = None, ""
        for fetch, name in ((ak.stock_info_global_em, "东财快讯"),
                            (ak.stock_info_global_cls, "财联社")):
            try:
                cand = fetch()
            except Exception as exc:
                logger.warning("%s电报获取失败 %s: %s", name, obj, exc)
                continue
            if cand is not None and not cand.empty:
                df, src = cand, name
                break
        if df is None:
            return []
        _telegraph_cache[as_of] = (now, (df, src))
    return _match_telegraph(df, obj, as_of, max_items, source=src)


def _match_telegraph(df, obj: str, as_of: str, max_items: int, source: str) -> list[dict]:
    """在电报快讯 DataFrame 内按主题关键词匹配，产出带真实来源/日期的 news 条目。"""
    if df is None or df.empty or "标题" not in df.columns:
        return []
    kw = (obj or "").strip()
    if not kw:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        title = str(row.get("标题") or "").strip()
        content = str(row.get("内容") or row.get("摘要") or "").strip()
        if kw not in f"{title} {content}":
            continue
        published = str(row.get("发布日期") or row.get("发布时间") or "")[:10]
        if not published or not _within_news_window(published, as_of):
            continue
        summary = (title or content[:80]).replace("\n", " ")[:200]
        out.append({
            "kind": "news",
            "source": source,
            "url": "",
            "published_at": published,
            "fetched_at": as_of,
            "summary": summary,
        })
        if len(out) >= max_items:
            break
    return out


def _date_from_url(url: str) -> str:
    """从 URL 提取真实发布日期（/2026/8/29/、20260829 等模式）；无日期返回 ""。

    只认 URL 自身携带的日期串，不编造；用于深查 web 新闻的发布日接地。
    """
    u = url or ""
    m = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})[/-]", u)
    if not m:
        m = re.search(r"(20\d{2})(\d{2})(\d{2})", u)
    if not m:
        return ""
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except ValueError:
        return ""


def deep_dive_news(obj: str, obj_type: str, as_of: str, max_items: int = 5) -> list[dict]:
    """深查专用 news（2026-08-31）：电报快讯优先；无匹配 → web 检索，
    **发布日期从 URL 提取（真实来源域名 + URL 日期串），无日期丢弃，不编造**。

    深查由用户在仪表盘主动触发（3–5 行业 + 候选池个股），搜索量受控
    （每标的 2 查询），官方搜索失败自动降级免费引擎。
    """
    del obj_type
    telegraph = _telegraph_industry_facts(obj, as_of)
    if telegraph:
        return telegraph
    from invest.agent.web_tools import web_search

    seen: set[str] = set()
    out: list[dict] = []
    for query in (f"{obj} 新闻 公告", f"{obj} 舆情 讨论"):
        try:
            raw = web_search(query, n=5)
        except Exception as exc:
            logger.warning("深查 web 检索失败 %s: %s", obj, exc)
            continue
        if not isinstance(raw, list):
            continue
        for hit in raw:
            url = str(hit.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            published = _date_from_url(url)
            if not published or not _within_news_window(published, as_of):
                continue
            source = str(hit.get("source") or "").strip() or _source_from_url(url)
            if not source:
                continue
            out.append({
                "kind": "news",
                "source": source,
                "url": url,
                "published_at": published,
                "fetched_at": as_of,
                "summary": str(hit.get("title") or hit.get("snippet") or "")[:200],
            })
            if len(out) >= max_items:
                return out
    return out


def extract_recent_facts(obj: str, obj_type: str, as_of: str) -> list[dict]:
    """生产路径：优先电报快讯（东财/财联社，真实来源+发布时间，主题关键词匹配）；
    电报无匹配退回 web 检索。失败抛错，由调用方记缺失，不编造。"""
    del obj_type
    telegraph = _telegraph_industry_facts(obj, as_of)
    if telegraph:
        return telegraph
    hits = _collect_web_hits(obj, as_of)
    if not hits:
        raise RuntimeError(f"未检索到 {obj} 带发布日与来源的近3-7日消息")
    try:
        refined = _llm_refine_news(obj, as_of, hits)
    except Exception as exc:
        logger.warning("消息 LLM 提炼失败，仅保留已带来源与日期的检索项: %s", exc)
        refined = hits
    out: list[dict] = []
    for row in refined:
        grounded = _ground_fact_to_hit(row, hits)
        if grounded is None:
            continue
        source = grounded["source"]
        published = grounded["published_at"]
        if not source or not _within_news_window(published, as_of):
            continue
        kind = grounded["kind"]
        if kind not in {"news", "announcement", "sentiment"}:
            kind = "news"
        out.append({
            "kind": kind,
            "source": source,
            "url": grounded["url"],
            "published_at": published,
            "fetched_at": as_of,
            "summary": grounded["summary"],
        })
    if not out:
        raise RuntimeError(f"{obj} 近3-7日消息提炼后无带来源与发布日的条目")
    return out


def _ground_fact_to_hit(row: dict, hits: list[dict]) -> dict | None:
    """每条提炼必须对上某条检索 hit 的 url（或同等来源键）；对不上丢弃，字段以 hit 为准。"""
    url = str(row.get("url") or "").strip()
    source = str(row.get("source") or "").strip()
    published = str(row.get("published_at") or "").strip()[:10]
    hit: dict | None = None
    if url:
        for candidate in hits:
            if str(candidate.get("url") or "").strip() == url:
                hit = candidate
                break
    if hit is None and not url and source and published:
        for candidate in hits:
            cand_url = str(candidate.get("url") or "").strip()
            if cand_url:
                continue
            if (
                str(candidate.get("source") or "").strip() == source
                and str(candidate.get("published_at") or "").strip()[:10] == published
            ):
                hit = candidate
                break
    if hit is None:
        return None
    kind = str(row.get("kind") or hit.get("kind") or "news")
    return {
        "kind": kind,
        "source": str(hit.get("source") or "").strip(),
        "url": str(hit.get("url") or "").strip(),
        "published_at": str(hit.get("published_at") or "").strip()[:10],
        "summary": str(row.get("summary") or hit.get("summary") or hit.get("title") or "")[:500],
    }


def _collect_news(
    obj: str,
    obj_type: str,
    as_of: str,
    news_fn: NewsFn | None,
    seq_start: int,
) -> tuple[list[EvidenceItem], int, bool]:
    items: list[EvidenceItem] = []
    seq = seq_start
    if news_fn is None:
        return items, seq, False
    try:
        raw = news_fn(obj, obj_type, as_of) or []
    except Exception as exc:
        logger.warning("新闻提炼失败 %s: %s", obj, exc)
        return items, seq, True
    for row in raw:
        source = str(row.get("source") or "").strip()
        if not source:
            continue
        published = str(row.get("published_at") or "")
        if not _within_news_window(published, as_of):
            continue
        kind = str(row.get("kind") or "news")
        if kind not in {"news", "announcement", "sentiment"}:
            kind = "news"
        payload = _strip_rank_keys({
            k: v for k, v in row.items()
            if k not in {"kind", "source", "url", "published_at", "fetched_at", "summary"}
        })
        items.append(EvidenceItem(
            evidence_id=_next_evidence_id(None, as_of, seq),
            kind=kind,
            source=source,
            url=str(row.get("url") or ""),
            published_at=published,
            fetched_at=str(row.get("fetched_at") or as_of),
            as_of=as_of,
            summary=str(row.get("summary") or "")[:500],
            payload=payload if isinstance(payload, dict) else {},
        ))
        seq += 1
    return items, seq, False


def _add_dim_evidence(
    evidence: list[EvidenceItem],
    as_of: str,
    seq: int,
    kind_source: str,
    summary: str,
    payload: dict,
) -> int:
    evidence.append(EvidenceItem(
        evidence_id=_next_evidence_id(None, as_of, seq),
        kind="dimension",
        source=kind_source,
        as_of=as_of,
        published_at=as_of,
        fetched_at=as_of,
        summary=summary,
        payload=_jsonable(payload),
    ))
    return seq + 1


def _strength(conn: sqlite3.Connection, obj: str, as_of: str) -> dict | None:
    run = _as_of_max(
        conn, "quant_strength", "run_date", as_of,
        "obj_type='industry' AND period='mid' AND obj=?", (obj,),
    )
    if not run:
        return None
    return _row(
        conn,
        """SELECT obj, rs, momentum, trend_stage FROM quant_strength
           WHERE obj_type='industry' AND period='mid' AND obj=? AND run_date=?""",
        (obj, run),
    )


def _rotation(conn: sqlite3.Connection, obj: str, as_of: str) -> dict | None:
    run = _as_of_max(
        conn, "quant_rotation", "run_date", as_of, "industry=?", (obj,),
    )
    if not run:
        return None
    return _row(
        conn,
        """SELECT industry, rank, lead_lag, turnover_share FROM quant_rotation
           WHERE industry=? AND run_date=?""",
        (obj, run),
    )


def _valuation(conn: sqlite3.Connection, obj: str, as_of: str) -> dict | None:
    run = _as_of_max(
        conn, "quant_valuation", "run_date", as_of, "obj=?", (obj,),
    )
    if not run:
        return None
    return _row(
        conn,
        """SELECT obj, pe_pct, pb_pct, crowding, crowding_state FROM quant_valuation
           WHERE obj=? AND run_date=?""",
        (obj, run),
    )


def _capital(conn: sqlite3.Connection, obj: str, as_of: str) -> dict | None:
    run = _as_of_max(
        conn, "quant_capital", "run_date", as_of,
        "obj_type='industry' AND obj=?", (obj,),
    )
    out: dict[str, Any] = {}
    if run:
        cap = _row(
            conn,
            """SELECT fund_type, style, confidence FROM quant_capital
               WHERE obj_type='industry' AND obj=? AND run_date=?""",
            (obj, run),
        )
        if cap:
            out.update(cap)
    flow_date = _as_of_max(
        conn, "sector_fund_flow", "date", as_of, "industry=?", (obj,),
    )
    if flow_date:
        flow = _row(
            conn,
            """SELECT main_net, main_net_pct FROM sector_fund_flow
               WHERE industry=? AND date=?""",
            (obj, flow_date),
        )
        if flow:
            out["main_net"] = flow.get("main_net")
            out["main_net_pct"] = flow.get("main_net_pct")
            out["flow_date"] = flow_date
    return out or None


def _pe_spread(conn: sqlite3.Connection, obj: str, as_of: str) -> dict | None:
    from invest.discipline.spread import spread_analysis, truncate_at_break

    rows = conn.execute(
        """SELECT date, pe FROM industry_valuation
           WHERE industry=? AND pe IS NOT NULL AND date<=? ORDER BY date""",
        (obj, as_of),
    ).fetchall()
    if not rows:
        return None
    import pandas as pd

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    cutoff = pd.Timestamp(as_of) - pd.DateOffset(years=5)
    hist = df[df["date"] >= cutoff]
    if hist.empty:
        return None
    current = float(df.iloc[-1]["pe"])
    hist, _dates, break_info = truncate_at_break(
        hist["pe"].reset_index(drop=True),
        hist["date"].astype(str).reset_index(drop=True),
        entity=obj,
    )
    if hist.empty:
        return None
    result = spread_analysis(hist, current)
    result["break"] = break_info
    result["industry"] = obj
    return {k: result[k] for k in ("ok", "current", "pct_rank", "z_score", "cheap") if k in result}


def _cycle(conn: sqlite3.Connection, obj: str, as_of: str, strength: dict | None, capital: dict | None, valuation: dict | None) -> dict | None:
    row = _row(
        conn,
        """SELECT industry, phase, notes, updated_at FROM industry_cycle
           WHERE industry=? AND (updated_at IS NULL OR updated_at<=?)""",
        (obj, as_of),
    )
    if row and row.get("phase"):
        return {"phase": row["phase"], "source": "industry_cycle", "notes": row.get("notes") or ""}
    rs = (strength or {}).get("rs")
    if rs is None:
        return None
    from invest.skills.sections.d30_cycle_position import _stage

    net = (capital or {}).get("main_net")
    pe = (valuation or {}).get("pe_pct")
    phase = _stage(
        float(rs),
        float(net) if net is not None else None,
        float(pe) if pe is not None else None,
    )
    return {"phase": phase, "source": "rule_stage"}


def _macro(conn: sqlite3.Connection, as_of: str) -> dict | None:
    from invest.discipline.macro_gate import env_factor

    row = _row(
        conn,
        """SELECT date, value FROM ratings WHERE kind='macro' AND date<=?
           ORDER BY date DESC LIMIT 1""",
        (as_of,),
    )
    env = row["value"] if row else "中性"
    factor = env_factor(env)
    out: dict[str, Any] = {
        "env": env,
        "env_factor": factor,
        "role": "减法",
        "note": "宏观只做减法，不给方向加分",
    }
    if row:
        out["rating_date"] = row["date"]
    try:
        from invest.discipline.macro_gate import check_env_retrigger
        retrigger = check_env_retrigger(conn, as_of=as_of)
        if retrigger.get("n"):
            out["retrigger"] = retrigger["triggers"]
    except Exception:
        pass
    return out


def build_industry_card(
    conn: sqlite3.Connection,
    industry: str,
    *,
    as_of: str,
    news_fn: NewsFn | None = None,
) -> FactCard:
    strength = _strength(conn, industry, as_of)
    if strength is not None and strength.get("rs") is None:
        strength = None
    rotation = _rotation(conn, industry, as_of)
    valuation = _valuation(conn, industry, as_of)
    capital = _capital(conn, industry, as_of)
    crowding = None
    if valuation and valuation.get("crowding") is not None:
        crowding = {
            "crowding": valuation.get("crowding"),
            "crowding_state": valuation.get("crowding_state") or "",
        }
    pe_spread = _pe_spread(conn, industry, as_of)
    if valuation is not None:
        valuation = dict(valuation)
        if pe_spread:
            valuation["pe_spread"] = pe_spread
    cycle = _cycle(conn, industry, as_of, strength, capital, valuation)
    macro = _macro(conn, as_of)
    dimensions = {
        "strength": _jsonable(strength),
        "rotation": _jsonable(rotation),
        "valuation": _jsonable(valuation),
        "crowding": _jsonable(crowding),
        "capital": _jsonable(capital),
        "cycle": _jsonable(cycle),
        "macro": _jsonable(macro),
    }
    missing = [name for name in DIMENSIONS if not dimensions.get(name)]
    seq = _evidence_seq_start(conn, as_of)
    evidence: list[EvidenceItem] = []
    if strength:
        seq = _add_dim_evidence(
            evidence, as_of, seq, "quant_strength",
            f"{industry} 中线RS {float(strength['rs']):+.1%}",
            strength,
        )
    if crowding:
        seq = _add_dim_evidence(
            evidence, as_of, seq, "quant_valuation",
            f"{industry} 拥挤度 {crowding.get('crowding_state') or crowding.get('crowding')}",
            crowding,
        )
    if cycle:
        seq = _add_dim_evidence(
            evidence, as_of, seq, "industry_cycle",
            f"{industry} 周期 {cycle.get('phase')}",
            cycle,
        )
    news_items, seq, news_failed = _collect_news(industry, "industry", as_of, news_fn, seq)
    if news_failed:
        missing.append("news")
    evidence.extend(news_items)
    return FactCard(
        obj_type="industry",
        obj=industry,
        as_of=as_of,
        data_version=_data_version(conn, as_of),
        rule_version=RULE_VERSION,
        dimensions=_strip_rank_keys(dimensions),
        missing=missing,
        evidence=evidence,
    )


def build_stock_card(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    as_of: str,
    news_fn: NewsFn | None = None,
) -> FactCard:
    from invest.agent.tools import cross_validate
    from invest.discipline.auto import auto_factor_score
    from invest.discipline.spread import price_spread

    spread = price_spread(conn, symbol, as_of=as_of)
    factors = auto_factor_score(conn, symbol, spread=spread, as_of=as_of)
    xv = cross_validate(conn, symbol, obj_type="stock", as_of=as_of)
    dimensions = {
        "spread": _jsonable({k: spread.get(k) for k in ("ok", "current", "pct_rank", "z_score", "cheap")}),
        "factors": _jsonable({
            "ok": factors.get("ok"),
            "eligible": factors.get("eligible"),
            "note": factors.get("note"),
            "per_factor": (factors.get("factor_result") or {}).get("per_factor")
            if factors.get("ok") else [],
        }),
        "cross_validate": _jsonable({
            "n_dimensions": xv.get("n_dimensions"),
            "dimensions": xv.get("dimensions"),
        }),
    }
    missing = [k for k, v in dimensions.items() if not v or (isinstance(v, dict) and not v.get("ok") and k == "spread" and not v.get("current"))]
    if not spread.get("ok") and "spread" not in missing:
        missing.append("spread")
    seq = _evidence_seq_start(conn, as_of)
    evidence: list[EvidenceItem] = []
    if spread.get("ok"):
        seq = _add_dim_evidence(
            evidence, as_of, seq, "daily_bars",
            f"{symbol} 价格分位 {spread.get('pct_rank')}",
            dimensions["spread"],
        )
    news_items, seq, news_failed = _collect_news(symbol, "stock", as_of, news_fn, seq)
    if news_failed:
        missing.append("news")
    evidence.extend(news_items)
    return FactCard(
        obj_type="stock",
        obj=symbol,
        as_of=as_of,
        data_version=_data_version(conn, as_of),
        rule_version=RULE_VERSION,
        dimensions=_strip_rank_keys(dimensions),
        missing=missing,
        evidence=evidence,
    )


def _evidence_match_key(item: EvidenceItem) -> tuple:
    url = (item.url or "").strip()
    if url:
        return ("url", item.kind, url)
    return ("src", item.kind, item.source, item.published_at or "")


def _evidence_row_values(item: EvidenceItem) -> tuple:
    return (
        item.kind, item.source, item.url, item.published_at,
        item.fetched_at, item.as_of, item.summary,
        json.dumps(_jsonable(item.payload), ensure_ascii=False),
    )


def _write_evidence(
    conn: sqlite3.Connection,
    card_id: int,
    item: EvidenceItem,
    as_of: str,
) -> str:
    """写入一条证据：本卡已有编号则 UPDATE；他卡占用则加号重试，绝不 REPLACE 改挂。"""
    preferred = item.evidence_id
    if preferred:
        row = conn.execute(
            "SELECT card_id FROM fact_evidence WHERE evidence_id=?", (preferred,),
        ).fetchone()
        if row and int(row["card_id"]) == card_id:
            conn.execute(
                """UPDATE fact_evidence SET kind=?, source=?, url=?, published_at=?,
                       fetched_at=?, as_of=?, summary=?, payload_json=?
                   WHERE evidence_id=? AND card_id=?""",
                (*_evidence_row_values(item), preferred, card_id),
            )
            return preferred
        if row:
            preferred = None
    seq = _evidence_seq_start(conn, as_of)
    for _ in range(10000):
        eid = preferred or _next_evidence_id(conn, as_of, seq)
        preferred = None
        try:
            conn.execute(
                """INSERT INTO fact_evidence(
                       evidence_id, card_id, kind, source, url, published_at,
                       fetched_at, as_of, summary, payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (eid, card_id, *_evidence_row_values(item)),
            )
            return eid
        except sqlite3.IntegrityError:
            seq += 1
    raise RuntimeError(f"无法分配 evidence_id as_of={as_of}")


def persist_card(conn: sqlite3.Connection, card: FactCard) -> int:
    started = False
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
        started = True
    try:
        payload = card.to_dict()
        existing = conn.execute(
            "SELECT id FROM fact_cards WHERE obj_type=? AND obj=? AND as_of=?",
            (card.obj_type, card.obj, card.as_of),
        ).fetchone()
        values = (
            json.dumps(_jsonable(payload["dimensions"]), ensure_ascii=False),
            json.dumps(payload["missing"], ensure_ascii=False),
            card.data_version,
            card.rule_version,
            card.obj_type,
            card.obj,
            card.as_of,
        )
        old_map: dict[tuple, str] = {}
        if existing:
            card_id = int(existing["id"])
            for r in conn.execute(
                "SELECT * FROM fact_evidence WHERE card_id=?", (card_id,),
            ):
                old_item = EvidenceItem(
                    evidence_id=r["evidence_id"],
                    kind=r["kind"],
                    source=r["source"] or "",
                    url=r["url"] or "",
                    published_at=r["published_at"] or "",
                    fetched_at=r["fetched_at"] or "",
                    as_of=r["as_of"],
                    summary=r["summary"] or "",
                )
                old_map[_evidence_match_key(old_item)] = r["evidence_id"]
            conn.execute(
                """UPDATE fact_cards SET dimensions_json=?, missing_json=?,
                       data_version=?, rule_version=?
                   WHERE obj_type=? AND obj=? AND as_of=?""",
                values,
            )
        else:
            cur = conn.execute(
                """INSERT INTO fact_cards(
                       dimensions_json, missing_json, data_version, rule_version,
                       obj_type, obj, as_of
                   ) VALUES(?,?,?,?,?,?,?)""",
                values,
            )
            card_id = int(cur.lastrowid)
        used: list[str] = []
        for item in card.evidence:
            key = _evidence_match_key(item)
            if key in old_map:
                item.evidence_id = old_map[key]
            eid = _write_evidence(conn, card_id, item, card.as_of)
            item.evidence_id = eid
            used.append(eid)
        if used:
            placeholders = ",".join("?" * len(used))
            conn.execute(
                f"DELETE FROM fact_evidence WHERE card_id=? AND evidence_id NOT IN ({placeholders})",
                (card_id, *used),
            )
        else:
            conn.execute("DELETE FROM fact_evidence WHERE card_id=?", (card_id,))
        card.card_id = card_id
        conn.commit()
        return card_id
    except Exception:
        if started:
            conn.rollback()
        raise


def _card_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> FactCard:
    evidence_rows = conn.execute(
        "SELECT * FROM fact_evidence WHERE card_id=? ORDER BY id", (row["id"],)
    ).fetchall()
    evidence = [
        EvidenceItem(
            evidence_id=r["evidence_id"],
            kind=r["kind"],
            source=r["source"] or "",
            url=r["url"] or "",
            published_at=r["published_at"] or "",
            fetched_at=r["fetched_at"] or "",
            as_of=r["as_of"],
            summary=r["summary"] or "",
            payload=json.loads(r["payload_json"] or "{}"),
        )
        for r in evidence_rows
    ]
    return FactCard(
        obj_type=row["obj_type"],
        obj=row["obj"],
        as_of=row["as_of"],
        data_version=row["data_version"] or "",
        rule_version=row["rule_version"] or "",
        dimensions=json.loads(row["dimensions_json"] or "{}"),
        missing=json.loads(row["missing_json"] or "[]"),
        evidence=evidence,
        card_id=row["id"],
    )


def lookup_evidence(conn: sqlite3.Connection, evidence_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT e.evidence_id, e.kind, e.source, e.url, e.published_at, e.as_of,
                  e.summary, e.card_id, c.obj_type, c.obj
           FROM fact_evidence e
           LEFT JOIN fact_cards c ON c.id = e.card_id
           WHERE e.evidence_id=?""",
        (evidence_id,),
    ).fetchone()
    return dict(row) if row else None


def load_card(conn: sqlite3.Connection, obj_type: str, obj: str, as_of: str) -> FactCard | None:
    row = conn.execute(
        "SELECT * FROM fact_cards WHERE obj_type=? AND obj=? AND as_of=?",
        (obj_type, obj, as_of),
    ).fetchone()
    if row is None:
        return None
    return _card_from_row(conn, row)


def discover_industries(conn: sqlite3.Connection, *, as_of: str) -> dict[str, Any]:
    run = _as_of_max(
        conn, "quant_strength", "run_date", as_of,
        "obj_type='industry' AND period='mid'",
    )
    if not run:
        return {"as_of": as_of, "obj_type": "industry", "rule_version": RULE_VERSION, "industries": []}
    rows = conn.execute(
        """SELECT obj, rs, trend_stage FROM quant_strength
           WHERE obj_type='industry' AND period='mid' AND run_date=?
           ORDER BY rs DESC""",
        (run,),
    ).fetchall()
    industries = []
    for row in rows:
        obj = row["obj"]
        flags: list[str] = []
        val = _valuation(conn, obj, as_of)
        crowding = (val or {}).get("crowding")
        pe_pct = (val or {}).get("pe_pct")
        if crowding is not None and float(crowding) >= 0.8:
            flags.append("crowded")
        if pe_pct is not None and float(pe_pct) <= 0.3:
            flags.append("cheap_valuation")
        if pe_pct is not None and float(pe_pct) >= 0.8:
            flags.append("expensive_valuation")
        if row["rs"] is not None and float(row["rs"]) > 0:
            flags.append("positive_rs")
        industries.append({
            "obj": obj,
            "rs": float(row["rs"]) if row["rs"] is not None else None,
            "trend_stage": row["trend_stage"],
            "flags": flags,
        })
    return {
        "as_of": as_of,
        "obj_type": "industry",
        "rule_version": RULE_VERSION,
        "industries": industries,
    }


def _pool_symbols(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT symbol FROM candidate_pool
           WHERE out_date IS NULL ORDER BY in_date, symbol""",
    ).fetchall()
    return [r["symbol"] for r in rows]


def deep_dive(
    conn: sqlite3.Connection,
    *,
    industries: list[str],
    as_of: str,
    symbols: list[str] | None = None,
    news_fn: NewsFn | None = None,
) -> dict[str, Any]:
    if not 3 <= len(industries) <= MAX_DEEP_INDUSTRIES:
        raise ValueError(f"深查行业须为 3–5 个，当前 {len(industries)}")
    if symbols is None:
        symbols = _pool_symbols(conn)
    else:
        symbols = list(symbols)
    if len(symbols) > MAX_DEEP_STOCKS:
        raise ValueError(f"深查个股不超过 {MAX_DEEP_STOCKS} 只")
    if news_fn is None:
        news_fn = extract_recent_facts
    # 边建边落库：序号按 as_of 已占用 evidence_id 递增，避免多卡复用同一批 EVID。
    industry_cards = []
    for name in industries:
        card = build_industry_card(conn, name, as_of=as_of, news_fn=news_fn)
        persist_card(conn, card)
        industry_cards.append(card)
    stock_cards = []
    for symbol in symbols:
        card = build_stock_card(conn, symbol, as_of=as_of, news_fn=news_fn)
        persist_card(conn, card)
        stock_cards.append(card)
    return {"industry_cards": industry_cards, "stock_cards": stock_cards}


def record_comparison(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    peer_set: list[str],
    conclusion: str,
    notes: str = "",
) -> dict[str, Any]:
    data_version = _data_version(conn, as_of)
    cur = conn.execute(
        """INSERT INTO comparison_records(
               as_of, peer_set_json, conclusion, notes, data_version, rule_version
           ) VALUES(?,?,?,?,?,?)""",
        (
            as_of,
            json.dumps(list(peer_set), ensure_ascii=False),
            conclusion,
            notes,
            data_version,
            RULE_VERSION,
        ),
    )
    conn.commit()
    return {
        "id": int(cur.lastrowid),
        "as_of": as_of,
        "peer_set": list(peer_set),
        "conclusion": conclusion,
        "notes": notes,
        "data_version": data_version,
        "rule_version": RULE_VERSION,
    }


def load_comparison(conn: sqlite3.Connection, rec_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM comparison_records WHERE id=?", (rec_id,)).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "as_of": row["as_of"],
        "peer_set": json.loads(row["peer_set_json"] or "[]"),
        "conclusion": row["conclusion"] or "",
        "notes": row["notes"] or "",
        "data_version": row["data_version"] or "",
        "rule_version": row["rule_version"] or "",
    }


def _dim(card: FactCard, path: tuple[str, ...]) -> Any:
    cur: Any = card.dimensions
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def detect_important_changes(
    conn: sqlite3.Connection,
    *,
    as_of: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM fact_cards WHERE as_of=? AND obj_type='industry'", (as_of,)
    ).fetchall()
    changes: list[dict[str, Any]] = []
    for row in rows:
        current = _card_from_row(conn, row)
        prev_row = conn.execute(
            """SELECT * FROM fact_cards
               WHERE obj_type=? AND obj=? AND as_of<?
               ORDER BY as_of DESC LIMIT 1""",
            (current.obj_type, current.obj, as_of),
        ).fetchone()
        if prev_row is None:
            continue
        previous = _card_from_row(conn, prev_row)
        notes: list[str] = []
        rs_new = _dim(current, ("strength", "rs"))
        rs_old = _dim(previous, ("strength", "rs"))
        if rs_new is not None and rs_old is not None and abs(float(rs_new) - float(rs_old)) >= RS_CHANGE:
            notes.append(f"中线RS {float(rs_old):+.1%}→{float(rs_new):+.1%}")
        state_new = _dim(current, ("crowding", "crowding_state"))
        state_old = _dim(previous, ("crowding", "crowding_state"))
        if state_new and state_old and state_new != state_old:
            notes.append(f"拥挤度 {state_old}→{state_new}")
        phase_new = _dim(current, ("cycle", "phase"))
        phase_old = _dim(previous, ("cycle", "phase"))
        if phase_new and phase_old and phase_new != phase_old:
            notes.append(f"周期 {phase_old}→{phase_new}")
        pe_new = _dim(current, ("valuation", "pe_pct"))
        pe_old = _dim(previous, ("valuation", "pe_pct"))
        if pe_new is not None and pe_old is not None and abs(float(pe_new) - float(pe_old)) >= PE_CHANGE:
            notes.append(f"PE分位 {float(pe_old):.0%}→{float(pe_new):.0%}")
        rank_new = _dim(current, ("rotation", "rank"))
        rank_old = _dim(previous, ("rotation", "rank"))
        if rank_new is not None and rank_old is not None and abs(int(rank_new) - int(rank_old)) >= RANK_CHANGE:
            notes.append(f"轮动排名 {int(rank_old)}→{int(rank_new)}")
        if not notes:
            continue
        changes.append({
            "obj": current.obj,
            "as_of": as_of,
            "prev_as_of": previous.as_of,
            "notes": notes,
            "evidence_ids": [e.evidence_id for e in current.evidence],
        })
    return changes


def format_change_digest(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return ""
    as_of = changes[0]["as_of"]
    lines = [f"【事实卡重要变化】as_of={as_of}"]
    for item in changes:
        lines.append(f"{item['obj']}：" + "；".join(item["notes"]))
        eids = " ".join(item.get("evidence_ids") or [])
        if eids:
            lines.append(f"  证据: {eids}")
    return "\n".join(lines)


def run_factcard_refresh(
    db: str,
    conn: sqlite3.Connection | None = None,
    *,
    as_of: str | None = None,
    push: bool = True,
    news_fn: NewsFn | None = None,
    notifier: Any | None = None,
) -> Any:
    from invest.scheduler import JobResult, _delivery_result

    own = conn is None
    if own:
        conn = connect(db)
    try:
        as_of = as_of or dt.date.today().isoformat()
        if news_fn is None:
            news_fn = extract_recent_facts
        finder = discover_industries(conn, as_of=as_of)
        for item in finder["industries"]:
            card = build_industry_card(conn, item["obj"], as_of=as_of, news_fn=news_fn)
            persist_card(conn, card)
        changes = detect_important_changes(conn, as_of=as_of)
        if not changes:
            return JobResult.ok("无重要变化", artifact="factcard_refresh")
        digest = format_change_digest(changes)
        if not push:
            return JobResult.ok(digest, artifact="factcard_refresh")
        if notifier is None:
            from invest.notifier import Notifier
            notifier = Notifier()
        raw = notifier.send_text(
            digest,
            key="factcard_change",
            return_results=True,
            message_kind="alert",
            message_id="factcard_refresh",
        )
        return _delivery_result(
            raw,
            success_detail=digest,
            failure_detail="事实卡变化投递失败",
            artifact="factcard_refresh",
        )
    finally:
        if own and conn is not None:
            conn.close()
