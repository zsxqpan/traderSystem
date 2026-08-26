"""社区热议（2026-08-23，d28 复用 opinion-analysis 方法论）。

必应搜索雪球/股吧讨论 → LLM 单轮提炼 2-3 条社区热议。
- 搜索/LLM 失败均回退，不阻断报告；
- 模块级缓存（TTL 10 分钟）避免同一次报告生成重复调用。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_COMMUNITY_TTL = 600.0
_cache: dict[str, tuple[float, str]] = {}


def _search(query: str, n: int = 5) -> list[dict]:
    try:
        from invest.agent.web_tools import web_search

        r = web_search(query, n=n)
    except Exception as exc:
        logger.warning("社区热议搜索失败 %s: %s", query[:40], exc)
        return []
    return r if isinstance(r, list) else []


def _fetch_material() -> str:
    """搜雪球/股吧热门讨论，返回按平台分组的素材。"""
    groups = []
    xq = _search("site:xueqiu.com 热议 股票", n=5)
    if xq:
        groups.append("【雪球】\n" + "\n".join(
            f"- {it.get('title', '')}｜{it.get('snippet', '')}" for it in xq))
    gb = _search("site:guba.eastmoney.com 热议", n=5)
    if gb:
        groups.append("【股吧】\n" + "\n".join(
            f"- {it.get('title', '')}｜{it.get('snippet', '')}" for it in gb))
    return "\n\n".join(groups)


def community_hot(db_path: str, n: int = 3, job: str = "daily_report") -> str:
    """返回【社区热议】文本；无素材/失败返回空串（不阻断报告）。"""
    now = time.time()
    cached = _cache.get(db_path)
    if cached and now - cached[0] < _COMMUNITY_TTL:
        return cached[1]
    material = _fetch_material()
    if not material:
        return ""
    out = ""
    try:
        from invest.agent.llm import LLMClient
        from invest.db import connect

        conn = connect(db_path)
        try:
            client = LLMClient(conn=conn)
            sys_prompt = (
                "你是财经社区观察编辑。从下面的雪球/股吧讨论素材中，挑选市场讨论度最高、最能代表"
                f"散户情绪（看多/看空/分歧）的 {n} 条。要求：\n"
                "- 每条输出一行：『主题｜一句话观点（带情绪倾向）｜平台』；\n"
                "- 优先热门个股、板块分歧、争议事件；忽略营销/广告/重复内容；\n"
                "- 素材不足或都无关紧要时，如实输出'今日社区无特别热议'，不要编造。"
            )
            out = client.run(system=sys_prompt, user=material[:6000], job=job, max_turns=1)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("社区热议 LLM 提炼失败，直列素材: %s", exc)
    if out and not out.startswith("[预算不足"):
        out = out.strip()
    else:
        # 兜底直列素材（只列条目行，截断）
        items = [ln.strip() for ln in material.splitlines() if ln.strip().startswith("- ")]
        out = "\n".join(items[:n]) or "（暂无社区热议素材）"
    _cache[db_path] = (now, out)
    return out
