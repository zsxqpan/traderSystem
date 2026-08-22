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


def web_search(query: str, n: int = 5) -> list[dict] | dict:
    """必应搜索：返回 [{title, url, snippet}]；失败返回 {"error": ...}。"""
    url = "https://cn.bing.com/search?" + urllib.parse.urlencode({"q": query, "count": max(1, min(int(n or 5), 10))})
    try:
        r = _session().get(url, headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=12)
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        logger.warning("web_search 失败: %s", exc)
        return {"error": f"搜索失败: {type(exc).__name__}"}

    items: list[dict] = []
    # 必应结果块：<li class="b_algo">...<h2><a href="URL">标题</a></h2>...<p>摘要</p>
    for block in re.findall(r'<li class="b_algo".*?</li>', html, re.DOTALL)[:max(1, min(int(n or 5), 10))]:
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
    if not items:
        return {"error": "未搜索到结果（可能被反爬，请换关键词）"}
    return items[:max(1, min(int(n or 5), 10))]


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
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return {"url": url, "text": text[:_FETCH_MAX_LEN]}
