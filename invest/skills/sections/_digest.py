"""盘前 LLM 环节（2026-08-22，d25/d26/d27 共享，job='premarket' 计入用量告警）。

两次调用：
1. overnight_analysis(db_path) -> str：外围数据行 → LLM 解读对 A 股影响（2-4 句）；
2. digest(db_path) -> dict：近 3 个自然日财联社电报素材 → LLM 输出 JSON
   {risk_items[], news{macro,stock,market_outside}, macro_changed, risk_summary}。

- 模块级缓存（_DIGEST_TTL=600s）避免同一次盘前生成重复调用；
- LLM 失败/JSON 解析失败 → 回退（解读返回 ""、digest 返回 {"ok": False}），不阻断报告。
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

_DIGEST_TTL = 600.0  # 汇总+暴雷结果缓存 10 分钟
_OA_TTL = 300.0      # 外围解读缓存 5 分钟
_digest_cache: dict[str, tuple[float, dict]] = {}
_oa_cache: dict[str, tuple[float, str]] = {}

# 素材窗口：近 cut_days 个自然日（2026-08-31 起不再限定「最近交易日 15:00 之后」——
# 周末/周一早报时段财联社无电报会让消息汇总整节为空；放宽后周末素材也能进入汇总）


def _recent_telegraph(cut_days: int = 3) -> list[str]:
    """财联社电报（近 cut_days 个自然日原始素材）。失败返回 []。"""
    try:
        from invest.report import _fetch_telegraph_lines

        lines = _fetch_telegraph_lines(days=cut_days)
    except Exception as exc:
        logger.warning("电报素材获取失败: %s", exc)
        return []
    return lines or []


# web 要闻补充查询（2026-08-31）：早报消息汇总在财联社电报之外补全网要闻；
# DeepSeek 官方搜索每次=一次模型调用（max_tokens 512），控制在 4 个查询 × 3 条。
_DIGEST_WEB_QUERIES = (
    "A股 今日要闻",
    "A股 宏观政策 监管",
    "隔夜美股 大宗商品 汇率",
    "A股 业绩预告 风险警示",
)


def _recent_web_hits(max_per_query: int = 3) -> list[str]:
    """web 检索近 1-2 日要闻（"标题（来源URL）"行）。失败/无结果降级为空，不阻断。"""
    from invest.agent.web_tools import web_search

    lines: list[str] = []
    for q in _DIGEST_WEB_QUERIES:
        try:
            raw = web_search(q, n=max_per_query)
        except Exception as exc:
            logger.warning("早报 web 检索失败 %s: %s", q, exc)
            continue
        if not isinstance(raw, list):
            continue
        for hit in raw[:max_per_query]:
            title = str(hit.get("title") or "").strip()
            url = str(hit.get("url") or "").strip()
            if not title:
                continue
            lines.append(f"{title}（{url}）")
    return lines


def _llm(conn, system: str, user: str, max_tokens: int | None = None) -> str:
    from invest.agent.llm import LLMClient

    client = LLMClient(conn=conn)
    # 2026-08-26：LLM 抖动重试 1 次（盘前偶发失败会让消息汇总整节降级）
    for attempt in (0, 1):
        try:
            out = client.run(system=system, user=user, job="premarket", max_turns=1,
                             max_tokens=max_tokens)
            if (out or "").strip():
                return out
        except Exception as exc:
            if attempt == 1:
                logger.warning("盘前 LLM 调用失败(重试后): %s", exc)
    return ""


def overnight_analysis(db_path: str) -> str:
    """外围数据 → LLM 解读（2-4 句）。失败返回 ""（报告省略该节）。"""
    now = time.time()
    cached = _oa_cache.get(db_path)
    if cached and now - cached[0] < _OA_TTL:
        return cached[1]
    try:
        from invest.data.global_snapshot import global_snapshot_rows
        from invest.db import connect

        rows = global_snapshot_rows()
        if not rows:
            return ""
        material = "；".join(
            f"{r['name']} {'+' if (r.get('pct') or 0) >= 0 else ''}{r['pct']:+.2f}%"
            if r.get("pct") is not None else f"{r['name']} {r['value']:.4f}"
            for r in rows
        )
        conn = connect(db_path)
        try:
            out = _llm(
                conn,
                "你是 A 股盘前策略分析师。根据隔夜外围市场表现（美股/富时A50/日韩/商品/汇率），"
                "给出对今日 A 股的 2-4 句影响解读：风格倾向、受益/承压方向。只许基于给出的数据推理，禁止编造数字。",
                f"隔夜外围: {material}",
            )
        finally:
            conn.close()
        out = (out or "").strip()
        _oa_cache[db_path] = (now, out)
        return out
    except Exception as exc:
        logger.warning("外围解读 LLM 失败: %s", exc)
        return ""


def digest(db_path: str) -> dict:
    """昨收后电报 → {ok, risk_items, news, macro_changed, risk_summary}。带缓存。"""
    now = time.time()
    cached = _digest_cache.get(db_path)
    if cached and now - cached[0] < _DIGEST_TTL:
        return cached[1]
    result: dict = {"ok": False}
    try:
        material = _recent_telegraph()
        material += _recent_web_hits()  # 2026-08-31：电报之外补全网要闻，提升宏观/海外/板块覆盖
        if not material:
            result["reason"] = "no_material"
            return result
        conn = None
        try:
            from invest.db import connect

            conn = connect(db_path)
            sys_prompt = (
                "你是 A 股盘前财经编辑。下面是财联社电报与全网要闻检索（近 3 个自然日）。\n"
                "任务：1) 筛选市场关注度高的关键消息（宏观政策/个股/市场外社会热点都算，如电影票房破圈这类");
            sys_prompt += (
                "也算）；2) 识别与个股相关的风险事件（业绩雷/司法雷/停牌核查/风险警示/异动监控/黑天鹅）。\n"
                "只输出一个 JSON 对象，不要任何其他文字：\n"
                '{"risk_items": [{"symbol":"6位代码或留空","name":"公司名","kind":"业绩雷|司法雷|停牌核查|风险提示|异动监控|黑天鹅",'
                '"event":"一句话事件","impact":"一句话影响"}],\n'
                '"news": {"macro": [{"title":"消息","impact":"一句话影响/机会"}],'
                '"stock": [{"title":"消息","impact":"..."}],'
                '"market_outside": [{"title":"消息","impact":"..."}]},\n'
                '"macro_changed": true或false（宏观环境较昨日是否有实质变化）,'
                '"risk_summary": "一句话风险提示总括（无风险则空字符串）"}\n'
                "消息只挑最重要的，每组不超过 4 条；impact 控制在 25 字内。"
            )
            out = _llm(conn, sys_prompt, "\n".join(material)[:6000])
            parsed = _parse_json(out)
            if parsed:
                result = {"ok": True, **parsed}
                _digest_cache[db_path] = (now, result)
                return result
            # 2026-08-26：JSON 解析失败（LLM 输出带噪音）重试 1 次
            out2 = _llm(conn, sys_prompt + "\n（上次输出 JSON 解析失败，请严格只输出 JSON 对象）",
                        "\n".join(material)[:6000])
            parsed2 = _parse_json(out2)
            if parsed2:
                result = {"ok": True, **parsed2}
                _digest_cache[db_path] = (now, result)
                return result
        finally:
            if conn is not None:
                conn.close()
    except Exception as exc:
        logger.warning("盘前电报汇总 LLM 失败: %s", exc)
    return result


def _parse_json(text: str | None) -> dict | None:
    """解析 LLM 输出 JSON（容忍 ```json 包裹与前后噪音）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t.removeprefix("json")
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        d = json.loads(t[start:end + 1])
        return d if isinstance(d, dict) else None
    except ValueError:
        return None


def digest_fallback_text(db_path: str, n: int = 6) -> str:
    """消息汇总降级素材（2026-08-26）：digest 失败时直列最近电报标题，避免整节'暂无素材'。

    返回格式化的文本行（含时间+标题），无素材返回 ""（调用方再显示兜底文案）。
    """
    try:
        material = _recent_telegraph()
    except Exception:
        return ""
    if not material:
        return ""
    lines = []
    for ln in material[:n]:
        # 行首 "YYYY-MM-DD HH:MM:SS | 标题" → 标题部分
        head, _, rest = ln.partition("|")
        title = (rest or head).strip()
        ts = head.strip()[-5:] if head.strip() else ""
        lines.append(f"  - {title}{f'（{ts}）' if ts else ''}")
    return "\n".join(lines) if lines else ""
