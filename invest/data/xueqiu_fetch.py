"""雪球 Playwright 采集（2026-08-25）：真实浏览器绕过阿里云 WAF 抓大V 主页动态 + 文章正文。

背景：雪球站内被阿里云 WAF 硬挡（requests 系抓不了正文，2026-08-24 实测）；
Playwright chromium 无头浏览器 + 反检测配置（真实 UA / 禁 automation 标志 /
navigator.webdriver 置 undefined / 先访问首页过 WAF 挑战）可成功抓取（已验证）。

- fetch_article(url)：单篇文章正文（标题/时间/正文/作者）；
- fetch_user_statuses(user_id_or_url, limit)：用户主页动态列表（标题/时间/URL/摘要）；
- XueqiuClient：一轮采集复用同一浏览器，避免每帖重启 chromium；
- 失败静默返回 None/[]；触发限流抛 RateLimited，由 harvest 整轮停采。
"""
from __future__ import annotations

import logging
import random
import re
import time

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_TIMEOUT_MS = 30_000
_WAIT_MS = 3_000

# 文章正文选择器（雪球文章页，已验证）
_ARTICLE_BODY_SEL = ".article__bd__detail"

_EXTRACT_STATUSES_JS = """
(uid) => {
  const cards = [...document.querySelectorAll('article')];
  const out = [];
  const seen = new Set();
  for (const c of cards) {
    const t = (c.innerText || '').trim();
    if (!t || t.length < 8) continue;
    const a = c.querySelector(`a[href*="/${uid}/"]`);
    let href = a ? (a.href || a.getAttribute('href') || '') : '';
    if (href && href.startsWith('/')) href = 'https://xueqiu.com' + href;
    if (!href || seen.has(href)) continue;
    seen.add(href);
    const timeEl = [...c.querySelectorAll('a, span, time')].find(x => {
      const s = (x.innerText || '').trim();
      return /^\\d{4}-\\d{2}-\\d{2}/.test(s) || /^\\d{2}-\\d{2}/.test(s) || s.startsWith('昨天');
    });
    const titleEl = c.querySelector('h3, h2');
    out.push({
      url: href,
      title: titleEl ? (titleEl.innerText || '').trim().slice(0, 120) : '',
      time: timeEl ? (timeEl.innerText || '').trim() : '',
      text: t.slice(0, 4000),
      snippet: t.slice(0, 400),
    });
  }
  return out;
}
"""


def _uid_from(user_id_or_url: str) -> str:
    uid = (user_id_or_url or "").strip()
    if "/" in uid:
        m = re.search(r"xueqiu\.com/u/(\d+)", uid)
        uid = m.group(1) if m else uid.rstrip("/").split("/")[-1]
    return uid if uid.isdigit() else ""


_WAF_MARKERS = (
    "访问过于频繁",
    "请完成验证",
    "滑动验证",
    "安全验证",
    "请稍后重试",
    "unusual traffic",
)


def _is_waf_text(body: str) -> bool:
    s = (body or "")[:800]
    return any(m in s for m in _WAF_MARKERS)


class XueqiuClient:
    """一轮采集共用一个浏览器。先过首页 WAF，再慢速点页。"""

    def __init__(
        self,
        *,
        wait_ms: int = _WAIT_MS,
        scroll_rounds: int | None = None,
        rng: random.Random | None = None,
        sleep_fn=None,
    ):
        self.wait_ms = wait_ms
        self.scroll_rounds = scroll_rounds
        self._rng = rng or random.Random()
        self._sleep = sleep_fn or time.sleep
        self._p = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._tabs = ("原发", "长文")

    def __enter__(self):
        self._launch()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def _launch(self) -> None:
        from playwright.sync_api import sync_playwright

        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._ctx = self._browser.new_context(
            user_agent=_UA,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        self._ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self._page = self._ctx.new_page()
        self._page.goto("https://xueqiu.com/", timeout=_TIMEOUT_MS, wait_until="domcontentloaded")
        self._page.wait_for_timeout(2000)
        if self._blocked():
            from invest.bigv.harvest import RateLimited

            raise RateLimited("首页 WAF/限流")

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._p is not None:
                self._p.stop()
        except Exception:
            pass
        self._p = self._browser = self._ctx = self._page = None

    def _blocked(self) -> bool:
        try:
            return _is_waf_text(self._page.inner_text("body"))
        except Exception:
            return True

    def _human_pause(self, lo: float = 2.2, hi: float = 5.5) -> None:
        self._sleep(self._rng.uniform(lo, hi))

    def _goto(self, url: str) -> None:
        self._human_pause(1.8, 4.0)
        self._page.goto(url, timeout=_TIMEOUT_MS, wait_until="domcontentloaded")
        self._page.wait_for_timeout(self.wait_ms)
        self._dismiss_popups()
        if self._blocked():
            from invest.bigv.harvest import RateLimited

            raise RateLimited("访问过于频繁")

    def _dismiss_popups(self) -> None:
        """关掉登录挡板，不登录。翻页接口未登录无效，只保证公开首页可点。"""
        try:
            self._page.evaluate(
                """() => {
                  const skip = [...document.querySelectorAll('a,button,span')]
                    .find(e => (e.innerText || '').trim() === '跳过');
                  if (skip) skip.click();
                  const dim = document.querySelector('.modals.dimmer');
                  if (dim) {
                    dim.classList.remove('js-shown');
                    dim.style.display = 'none';
                  }
                }"""
            )
            self._page.wait_for_timeout(400)
        except Exception:
            pass

    def fetch_article(self, url: str) -> dict | None:
        if not url or "xueqiu.com" not in url:
            return None
        if self._page is None:
            self._launch()
        try:
            self._goto(url)
            title = (self._page.title() or "").strip()
            author = ""
            m = re.search(r"来自([^\s·]+)的雪球专栏", self._page.inner_text("body")[:500])
            if m:
                author = m.group(1)
            text = ""
            el = self._page.query_selector(_ARTICLE_BODY_SEL)
            if el:
                text = el.inner_text().strip()
            if not text:
                text = self._page.inner_text("body")
            t = re.search(r"发布于\s*(\d{4}-\d{2}-\d{2}[^ ]*(?:\s*\d{2}:\d{2})?)", text[:400])
            pub_time = t.group(1) if t else ""
            text = text[:8000]
            return {"url": url, "title": title[:200], "time": pub_time, "author": author,
                    "text": text, "length": len(text)}
        except Exception as exc:
            from invest.bigv.harvest import RateLimited

            if isinstance(exc, RateLimited):
                raise
            logger.warning("雪球文章抓取失败 %s: %s", url[:60], exc)
            return None

    def fetch_user_statuses(
        self,
        user_id_or_url: str,
        limit: int = 10,
        *,
        original_only: bool = True,
        scroll_rounds: int | None = None,
        tabs: tuple[str, ...] | None = None,
    ) -> list[dict]:
        uid = _uid_from(user_id_or_url)
        if not uid:
            return []
        limit = max(1, min(int(limit or 10), 50))
        if scroll_rounds is None:
            scroll_rounds = self.scroll_rounds
        rounds = scroll_rounds if scroll_rounds is not None else min(8, max(2, (limit + 4) // 5))
        if self._page is None:
            self._launch()
        url = f"https://xueqiu.com/u/{uid}"
        self._goto(url)
        use_tabs = tabs or (self._tabs if original_only else ("原发",))
        if original_only is False:
            use_tabs = tabs or ("",)
        out: list[dict] = []
        seen: set[str] = set()
        for tab in use_tabs:
            if tab:
                try:
                    clicked = self._page.evaluate(
                        """(name) => {
                          const el = [...document.querySelectorAll('a,button,span,div')]
                            .find(e => (e.innerText || '').trim() === name);
                          if (!el) return false;
                          el.click();
                          return true;
                        }""",
                        tab,
                    )
                    if clicked:
                        self._page.wait_for_timeout(1800)
                        self._human_pause(1.2, 2.4)
                except Exception:
                    logger.info("雪球筛选未点到 %s %s", uid, tab)
            stale = 0
            for i in range(rounds + 1):
                try:
                    rows = self._page.evaluate(_EXTRACT_STATUSES_JS, uid) or []
                except Exception as exc:
                    logger.warning("雪球主页解析失败 %s: %s", uid, exc)
                    rows = []
                grew = 0
                for it in rows:
                    href = str((it or {}).get("url") or "").strip()
                    if not href or href in seen:
                        continue
                    seen.add(href)
                    grew += 1
                    out.append({
                        "url": href,
                        "title": str(it.get("title") or "")[:200],
                        "time": str(it.get("time") or ""),
                        "text": str(it.get("text") or ""),
                        "snippet": str(it.get("snippet") or ""),
                    })
                    if len(out) >= limit:
                        return out
                if i >= rounds:
                    break
                if grew == 0:
                    stale += 1
                    if stale >= 2:
                        break
                else:
                    stale = 0
                try:
                    self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    self._page.wait_for_timeout(int(1200 + self._rng.uniform(500, 1800)))
                except Exception:
                    break
        if not out:
            links = self._page.eval_on_selector_all(
                f'a[href^="/{uid}/"]',
                "els => els.map(e => ({href: e.href, text: (e.innerText || '').trim()}))",
            )
            for it in links or []:
                href = str(it.get("href") or "")
                text = str(it.get("text") or "").strip()
                if not text or href in seen:
                    continue
                seen.add(href)
                out.append({"url": href, "title": text[:200], "time": "", "snippet": "", "text": text})
                if len(out) >= limit:
                    break
        return out


def fetch_article(url: str) -> dict | None:
    """抓单篇文章正文。返回 {url, title, time, author, text, length}；失败 None。"""
    if not url or "xueqiu.com" not in url:
        return None
    try:
        with XueqiuClient() as cli:
            return cli.fetch_article(url)
    except Exception as exc:
        from invest.bigv.harvest import RateLimited

        if isinstance(exc, RateLimited):
            raise
        logger.warning("雪球文章抓取失败 %s: %s", url[:60], exc)
        return None


def fetch_user_statuses(
    user_id_or_url: str,
    limit: int = 10,
    *,
    original_only: bool = True,
    scroll_rounds: int | None = None,
) -> list[dict]:
    """抓用户主页动态列表。返回 [{title, url, time, snippet, text}]；失败 []。"""
    uid = _uid_from(user_id_or_url)
    if not uid:
        return []
    try:
        with XueqiuClient() as cli:
            return cli.fetch_user_statuses(
                uid, limit, original_only=original_only, scroll_rounds=scroll_rounds,
            )
    except Exception as exc:
        from invest.bigv.harvest import RateLimited

        if isinstance(exc, RateLimited):
            raise
        logger.warning("雪球主页抓取失败 %s: %s", uid, exc)
        return []
