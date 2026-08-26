"""Agent 联网工具（2026-08-21）：web_search 搜索结果 + web_fetch 抓取正文。

- 必应国内版（cn.bing.com）HTML 搜索，无 API key、无 JS 依赖；
- 全部 trust_env=False（绕 WinINET 系统代理 127.0.0.1:7892）；
- 结果截断控制 token；失败静默返回空/错误说明（不阻断对话）。
"""
from __future__ import annotations

import logging
import re
import urllib.parse

import requests

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_MAX_RESULTS = 5
_RESULT_MAX_LEN = 120
_FETCH_MAX_LEN = 3000


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    return s


def web_search_deepseek(query: str, n: int = 5) -> list[dict] | None:
    """DeepSeek 官方联网搜索（2026-08-25）：Anthropic 兼容 /messages + web_search_20250305 原生工具。

    服务端搜索返回结构化 web_search_tool_result（url/title/page_age）+ text block 的
    citations[]（url → cited_text 摘要）。**每次搜索 = 一次完整模型调用（消耗 LLM token）**。
    未配置 key / 请求失败 / 未触发原生搜索 → 返回 None（调用方降级多引擎 HTML）。
    """

    from invest.config import get_settings

    s = get_settings()
    if not s.llm_api_key:
        return None
    base = (s.llm_base_url or "https://api.deepseek.com").rstrip("/")
    base = base.replace("/v1", "") + "/anthropic/v1"
    url = base + "/messages"
    body = {
        "model": s.llm_model or "deepseek-v4-flash",
        "max_tokens": 512,
        "messages": [{"role": "user",
                      "content": [{"type": "text",
                                   "text": f"Perform a web search for the query: {query}"}]}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
    }
    try:
        r = _session().post(
            url, json=body, timeout=30,
            headers={"Content-Type": "application/json", "x-api-key": s.llm_api_key,
                     "authorization": f"Bearer {s.llm_api_key}",
                     "anthropic-version": "2023-06-01", "accept": "application/json"},
        )
        r.raise_for_status()
        j = r.json()
    except Exception as exc:
        logger.warning("DeepSeek 官方搜索失败: %s", exc)
        return None
    blocks = j.get("content") or []
    result_blocks = [b for b in blocks if b.get("type") == "web_search_tool_result"]
    if not result_blocks:
        logger.warning("DeepSeek 官方搜索未触发（无 web_search_tool_result）")
        return None
    # citations[] → url → cited_text（snippet 来源）
    snippets: dict[str, str] = {}
    for b in blocks:
        if b.get("type") != "text":
            continue
        for cite in b.get("citations") or []:
            u, ct = cite.get("url"), cite.get("cited_text")
            if u and ct and u not in snippets:
                snippets[u] = ct
    seen: set[str] = set()
    out: list[dict] = []
    for b in result_blocks:
        for it in b.get("content") or []:
            if not isinstance(it, dict) or it.get("type") != "web_search_result":
                continue
            u = it.get("url") or ""
            if not u or u in seen:
                continue
            seen.add(u)
            out.append({
                "title": (it.get("title") or "")[:_RESULT_MAX_LEN],
                "url": u[:_RESULT_MAX_LEN],
                "snippet": (snippets.get(u) or "")[:_RESULT_MAX_LEN],
            })
    if not out:
        return None
    return out[:max(1, int(n or 5))]


def web_search(query: str, n: int = 5, engine: str | None = None) -> list[dict] | dict:
    """联网搜索（2026-08-25）：**DeepSeek 官方搜索优先**（Anthropic /messages + web_search 原生工具），
    失败/未触发降级多引擎 HTML（必应cn → 搜狗 → 360 → 百度，含质量检测）。

    site: 查询强制走必应（官方搜索与百度/搜狗不支持 site: 操作符）。
    返回 [{title, url, snippet}]；全部失败返回 {"error": ...}。
    """
    n = max(1, min(int(n or 5), 10))
    if "site:" in (query or ""):
        engine = "bing_cn"
    if engine is None:
        ds = web_search_deepseek(query, n)
        if ds:
            return ds
    order = [engine] if engine else _ENGINE_ORDER
    last_items: list[dict] | None = None
    for eng in order:
        try:
            items = _search_engine(eng, query, n)
        except Exception as exc:
            logger.warning("web_search %s 失败: %s", eng, exc)
            continue
        if isinstance(items, list):
            last_items = items
            if _quality_ok(query, items):
                return items[:n]
            logger.warning("web_search %s 结果质量差（%d 条），降级下一引擎: %s",
                           eng, len(items), query[:30])
    if last_items:
        # 2026-08-25：全引擎质量差（如必应把中文拆成单字词典词条）→ 不返回垃圾，
        # 明确提示换关键词，让模型按规则 1 重试
        return {"error": "搜索结果质量差（可能被分词/反爬），请换关键词重试（加股票代码/全称/限定词）"}
    return {"error": "多引擎搜索均失败"}


# 引擎链（首个质量达标即返回；2026-08-25：百度对无浏览器 requests 直接返回"安全验证"验证码页
# ——硬反爬无法绕过，降到最后并快速跳过；搜狗/360 无 cookie 稳定可爬）
_ENGINE_ORDER = ("bing_cn", "sogou", "so360", "baidu")
_DICT_URL = re.compile(r"(zdic\.net|hanyuguoxue\.com|shidianguji\.com|dict\.|baike\.baidu\.com/item|baike\.sogou\.com)")
_CODE_RE = re.compile(r"\d{6}")


def _quality_ok(query: str, items: list[dict]) -> bool:
    """结果质量检测：含 6 位代码 → 结果必须命中该代码；否则排除词典 URL 且标题须含
    查询词的 2 字连续片段（如"孚日股份"→ 标题含"孚日/日股/股份"任一）——防止词典内容
    藏在任意域名（zhihu/百家号等）绕过。"""
    if not items:
        return False
    codes = _CODE_RE.findall(query or "")
    # 查询词中文 2 字连续片段（bigrams）
    bigrams: set[str] = set()
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", query or ""):
        for i in range(len(seg) - 1):
            bigrams.add(seg[i:i + 2])
    for it in items:
        blob = f"{it.get('title', '')} {it.get('url', '')} {it.get('snippet', '')}"
        if codes and any(c in blob for c in codes):
            return True
        if not codes:
            title = it.get("title") or ""
            if _DICT_URL.search(it.get("url", "")):
                continue
            if bigrams and not any(b in title for b in bigrams):
                continue  # 标题与查询词无关（如单字词典条目）
            if title.strip():
                return True
    return False


def _search_engine(engine: str, query: str, n: int) -> list[dict]:
    """按引擎搜索。bing_cn=必应国内；baidu=百度；sogou=搜狗。"""
    s = _session()
    if engine == "baidu":
        url = "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query})
        r = s.get(url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=12)
        r.raise_for_status()
        r.encoding = "utf-8"
        # 2026-08-25：百度无浏览器环境返回"安全验证"验证码页（需 JS/指纹）——识别后快速跳过
        if len(r.text) < 5000 or "安全验证" in r.text[:3000]:
            return []
        items = []
        for blk in re.findall(r"<h3[^>]*>.*?</h3>", r.text, re.DOTALL)[:n]:
            m = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.DOTALL)
            if m:
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if title:
                    items.append({"title": title[:_RESULT_MAX_LEN],
                                  "url": m.group(1)[:_RESULT_MAX_LEN], "snippet": ""})
        abs_blocks = re.findall(r'class="(?:c-abstract|c-span-last|content-right_)[^"]*"[^>]*>(.*?)</(?:span|div)>',
                                r.text, re.DOTALL)
        for i, ab in enumerate(abs_blocks[:n]):
            if i < len(items):
                txt = re.sub(r"<[^>]+>", "", ab).strip()
                items[i]["snippet"] = txt[:_RESULT_MAX_LEN]
        return items
    if engine == "so360":
        url = "https://www.so.com/s?" + urllib.parse.urlencode({"q": query})
        r = s.get(url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=12)
        r.raise_for_status()
        r.encoding = "utf-8"
        items = []
        for blk in re.findall(r"<h3[^>]*>.*?</h3>", r.text, re.DOTALL)[:n]:
            m = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.DOTALL)
            if m:
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if title:
                    items.append({"title": title[:_RESULT_MAX_LEN],
                                  "url": m.group(1)[:_RESULT_MAX_LEN], "snippet": ""})
        abs_blocks = re.findall(r'class="(?:res-desc|res-comm-con|res-comm)[^"]*"[^>]*>(.*?)</(?:p|div)>',
                                r.text, re.DOTALL)
        for i, ab in enumerate(abs_blocks[:n]):
            if i < len(items):
                txt = re.sub(r"<[^>]+>", "", ab).strip()
                items[i]["snippet"] = txt[:_RESULT_MAX_LEN]
        return items
    if engine == "sogou":
        url = "https://www.sogou.com/web?" + urllib.parse.urlencode({"query": query})
        r = s.get(url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=12)
        r.raise_for_status()
        r.encoding = "utf-8"
        items = []
        for blk in re.findall(r"<h3[^>]*>.*?</h3>", r.text, re.DOTALL)[:n]:
            m = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.DOTALL)
            if m:
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if title:
                    url = m.group(1)
                    if url.startswith("/"):
                        url = "https://www.sogou.com" + url
                    items.append({"title": title[:_RESULT_MAX_LEN],
                                  "url": url[:_RESULT_MAX_LEN], "snippet": ""})
        # 补摘要（搜狗 str_info / text-layout 块）
        abs_blocks = re.findall(r'class="(?:str_info|fz-mid text-layout|star-wiki|space-txt)[^"]*"[^>]*>(.*?)</(?:p|div)>',
                                r.text, re.DOTALL)
        for i, ab in enumerate(abs_blocks[:n]):
            if i < len(items):
                txt = re.sub(r"<[^>]+>", "", ab).strip()
                items[i]["snippet"] = txt[:_RESULT_MAX_LEN]
        return items
    # bing_cn（原实现）
    url = "https://cn.bing.com/search?" + urllib.parse.urlencode({"q": query, "count": n})
    r = s.get(url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=12)
    r.raise_for_status()
    html = r.text
    items = []
    for block in re.findall(r'<li class="b_algo".*?</li>', html, re.DOTALL)[:n]:
        m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not m:
            continue
        href = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        snippet = re.sub(r"<[^>]+>", "", p.group(1)).strip() if p else ""
        items.append({
            "title": title[:_RESULT_MAX_LEN],
            "url": href,
            "snippet": snippet[:_RESULT_MAX_LEN],
        })
    return items


def web_fetch(url: str) -> dict:
    """抓取网页正文：返回 {url, text}（去脚本/样式/标签，截断）；失败返回 {error}。"""
    if not url.startswith(("http://", "https://")):
        return {"error": "URL 必须以 http(s):// 开头"}
    try:
        r = _session().get(url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=12)
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        logger.warning("web_fetch 失败 %s: %s", url[:60], exc)
        return {"error": f"抓取失败: {type(exc).__name__}"}
    # 2026-08-24：阿里云 WAF 挑战页（如雪球）——正文不可抓，明确提示
    if "_waf_" in html[:800] or "aliyun_waf" in html[:800]:
        return {"url": url, "error": "目标页面受 WAF 保护（如雪球站内），无法抓取正文；"
                                    "可用 xueqiu_search 获取搜索摘要，或浏览器打开链接查看"}
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return {"url": url, "text": text[:_FETCH_MAX_LEN]}


def xueqiu_search(keyword: str, n: int = 5) -> list[dict] | dict:
    """搜索雪球文章/大V（2026-08-24）：必应 `site:xueqiu.com {keyword}`，过滤出雪球链接。

    雪球站内被 WAF 保护无法抓正文，但必应索引了文章标题/摘要/URL——这是当前获取
    雪球信息（某大V 近期文章、热门讨论）的可行渠道。返回 [{title, url, snippet}]。
    """
    r = web_search(f"site:xueqiu.com {keyword}", n=max(5, int(n or 5) * 2))
    if not isinstance(r, list):
        return r
    out = [it for it in r if "xueqiu.com" in (it.get("url") or "")]
    return out[:max(1, int(n or 5))] or {"error": f"未搜到雪球相关内容（{keyword}）"}
