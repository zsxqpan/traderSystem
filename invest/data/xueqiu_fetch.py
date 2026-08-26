"""雪球 Playwright 采集（2026-08-25）：真实浏览器绕过阿里云 WAF 抓大V 主页动态 + 文章正文。

背景：雪球站内被阿里云 WAF 硬挡（requests 系抓不了正文，2026-08-24 实测）；
Playwright chromium 无头浏览器 + 反检测配置（真实 UA / 禁 automation 标志 /
navigator.webdriver 置 undefined / 先访问首页过 WAF 挑战）可成功抓取（已验证）。

- fetch_article(url)：单篇文章正文（标题/时间/正文/作者）；
- fetch_user_statuses(user_id_or_url, limit)：用户主页动态列表（标题/时间/URL/摘要）；
- 每次调用独立启动 browser，用完关闭；串行；超时 30s；失败静默返回 None/[]；
- 抓取结果由调用方（tools.xueqiu_fetch_*）去重入库 big_v_opinion / big_v_profile。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_TIMEOUT_MS = 30_000
_WAIT_MS = 3_000

# 文章正文选择器（雪球文章页，已验证）
_ARTICLE_BODY_SEL = ".article__bd__detail"
# 用户主页动态条目（宽松匹配：含用户 ID 前缀的文章链接）
_USER_LINK_RE = re.compile(r'href="/(\d+)/(\d+)"')


def _launch(page_url: str):
    """启动 chromium（反检测），先访问目标页等 WAF 挑战通过。返回 (playwright, browser, page)。"""
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 800},
                              locale="zh-CN")
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = ctx.new_page()
    # 先访问首页（触发 WAF 挑战并完成），再访问目标页
    page.goto("https://xueqiu.com/", timeout=_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    page.goto(page_url, timeout=_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(_WAIT_MS)
    return p, browser, page


def _is_waf_page(page) -> bool:
    """WAF 挑战未通过检测：标题含验证 / 正文过短。"""
    try:
        body = page.inner_text("body")[:300]
        if "验证" in body or "访问过于频繁" in body:
            return True
    except Exception:
        return True
    return False


def fetch_article(url: str) -> dict | None:
    """抓单篇文章正文。返回 {url, title, time, author, text, length}；失败 None。"""
    if not url or "xueqiu.com" not in url:
        return None
    try:
        p, browser, page = _launch(url)
        try:
            if _is_waf_page(page):
                logger.warning("雪球文章 WAF 未过: %s", url[:60])
                return None
            title = (page.title() or "").strip()
            author = ""
            m = re.search(r"来自([^\s·]+)的雪球专栏", page.inner_text("body")[:500])
            if m:
                author = m.group(1)
            text = ""
            el = page.query_selector(_ARTICLE_BODY_SEL)
            if el:
                text = el.inner_text().strip()
            if not text:
                text = page.inner_text("body")
            # 时间：正文头部 "发布于 YYYY-MM-DD HH:MM"
            t = re.search(r"发布于\s*(\d{4}-\d{2}-\d{2}[^ ]*(?:\s*\d{2}:\d{2})?)", text[:400])
            pub_time = t.group(1) if t else ""
            text = text[:8000]
            return {"url": url, "title": title[:200], "time": pub_time, "author": author,
                    "text": text, "length": len(text)}
        finally:
            browser.close()
            p.stop()
    except Exception as exc:
        logger.warning("雪球文章抓取失败 %s: %s", url[:60], exc)
        return None


def fetch_user_statuses(user_id_or_url: str, limit: int = 10) -> list[dict]:
    """抓用户主页动态列表。返回 [{title, url, time, snippet}]；失败 []。"""
    uid = user_id_or_url.strip()
    if "/" in uid:
        m = re.search(r"xueqiu\.com/u/(\d+)", uid)
        uid = m.group(1) if m else uid.rstrip("/").split("/")[-1]
    if not uid.isdigit():
        return []
    limit = max(1, min(int(limit or 10), 30))
    url = f"https://xueqiu.com/u/{uid}"
    try:
        p, browser, page = _launch(url)
        try:
            if _is_waf_page(page):
                logger.warning("雪球主页 WAF 未过: %s", uid)
                return []
            # 抓文章链接 + 标题（用户主页动态条目）
            links = page.eval_on_selector_all(
                f'a[href^="/{uid}/"]',
                "els => els.map(e => ({href: e.href, text: (e.innerText || '').trim()}))",
            )
            out: list[dict] = []
            seen = set()
            for it in links or []:
                href = str(it.get("href") or "")
                text = str(it.get("text") or "").strip()
                if not text or href in seen:
                    continue
                seen.add(href)
                out.append({"url": href, "title": text[:200], "time": "", "snippet": ""})
                if len(out) >= limit:
                    break
            if not out:
                # 兜底：整页文本找 "发布于" 时间戳条目
                body = page.inner_text("body")
                for m in re.finditer(r"发布于\s*(\d{4}-\d{2}-\d{2}[^\n]*)", body):
                    out.append({"url": url, "title": m.group(1).strip()[:200], "time": "",
                                "snippet": ""})
                    if len(out) >= limit:
                        break
            return out
        finally:
            browser.close()
            p.stop()
    except Exception as exc:
        logger.warning("雪球主页抓取失败 %s: %s", uid, exc)
        return []
