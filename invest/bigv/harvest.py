"""雪球增量采集：只跟 watched=1；抓取函数可注入。"""
from __future__ import annotations

import datetime as dt
import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any

from invest.bigv.persist import insert_opinion
from invest.bigv.schema import ensure_bigv_schema
from invest.bigv.watch import list_watched

logger = logging.getLogger(__name__)

RECENT_N = 15
BACKFILL_N = 30
TIMELINE_ENOUGH = 40


class RateLimited(Exception):
    """雪球限流/WAF，整轮停采，次日再续。"""


def _default_statuses(uid: str, limit: int = 15) -> list[dict]:
    from invest.data.xueqiu_fetch import fetch_user_statuses

    return fetch_user_statuses(uid, limit=limit) or []


def _default_article(url: str) -> dict | None:
    from invest.data.xueqiu_fetch import fetch_article

    return fetch_article(url)


def _norm_day(raw: str, today: dt.date) -> str:
    s = (raw or "").replace("修改于", "").strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    if s.startswith("昨天") or "昨天" in s[:4]:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"^(\d{1,2})-(\d{1,2})", s)
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
        year = today.year
        if (mm, dd) > (today.month, today.day):
            year -= 1
        return f"{year:04d}-{mm:02d}-{dd:02d}"
    return today.isoformat() if s else ""


def _clean_timeline(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"[\n\s]*(转发|收藏)\s*$", "", t)
    t = t.replace("展开", "").strip()
    return t[:8000]


def _timeline_enough(st: dict) -> bool:
    raw = st.get("text") or st.get("snippet") or ""
    if "展开" in raw:
        return False
    return len(_clean_timeline(raw)) >= TIMELINE_ENOUGH


def _status_article(st: dict, today: dt.date) -> dict:
    text = _clean_timeline(st.get("text") or st.get("snippet") or "")
    return {
        "url": st.get("url") or "",
        "title": (st.get("title") or "")[:200],
        "time": _norm_day(st.get("time") or "", today),
        "text": text,
    }


def harvest(
    conn,
    *,
    fetch_statuses: Callable[[str, int], list[dict]] | None = None,
    fetch_article: Callable[[str], dict | None] | None = None,
    limit: int | None = None,
    budget_s: float = 900,
    backfill: bool = False,
    monotonic: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    gap_s: float = 1.0,
    gap_jitter_s: float = 0.0,
    person_gap_s: float = 0.0,
    max_new_per_person: int = 0,
    today: dt.date | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """采集 watched 名单。返回 {new, people, stopped_budget, errors}。"""
    ensure_bigv_schema(conn)
    use_browser = fetch_statuses is None and fetch_article is None
    if use_browser:
        from invest.data.xueqiu_fetch import XueqiuClient

        with XueqiuClient(scroll_rounds=12 if backfill else 4) as cli:
            return _harvest_loop(
                conn,
                fetch_st=cli.fetch_user_statuses,
                fetch_art=cli.fetch_article,
                limit=limit,
                budget_s=budget_s,
                backfill=backfill,
                monotonic=monotonic,
                sleep_fn=sleep_fn,
                gap_s=gap_s,
                gap_jitter_s=gap_jitter_s,
                person_gap_s=person_gap_s,
                max_new_per_person=max_new_per_person,
                today=today,
                rng=rng,
            )
    return _harvest_loop(
        conn,
        fetch_st=fetch_statuses or (lambda uid, lim=RECENT_N: _default_statuses(uid, lim)),
        fetch_art=fetch_article or _default_article,
        limit=limit,
        budget_s=budget_s,
        backfill=backfill,
        monotonic=monotonic,
        sleep_fn=sleep_fn,
        gap_s=gap_s,
        gap_jitter_s=gap_jitter_s,
        person_gap_s=person_gap_s,
        max_new_per_person=max_new_per_person,
        today=today,
        rng=rng,
    )


def _harvest_loop(
    conn,
    *,
    fetch_st: Callable[[str, int], list[dict]],
    fetch_art: Callable[[str], dict | None],
    limit: int | None,
    budget_s: float,
    backfill: bool,
    monotonic: Callable[[], float] | None,
    sleep_fn: Callable[[float], None] | None,
    gap_s: float,
    gap_jitter_s: float,
    person_gap_s: float,
    max_new_per_person: int,
    today: dt.date | None,
    rng: random.Random | None,
) -> dict[str, Any]:
    mono = monotonic or time.monotonic
    sleep = sleep_fn or time.sleep
    roll = rng or random.Random()
    day0 = today or dt.date.today()
    n = int(limit or (BACKFILL_N if backfill else RECENT_N))
    n = max(1, min(n, 50))
    cap = int(max_new_per_person or 0)
    deadline = mono() + float(budget_s)
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_new = 0
    errors: list[str] = []
    stopped = False
    people = 0
    watched = list_watched(conn)

    for i, prof in enumerate(watched):
        if mono() >= deadline:
            stopped = True
            break
        uid = (prof.get("xueqiu_id") or "").strip()
        pid = prof["id"]
        if not uid:
            conn.execute(
                "UPDATE big_v_profile SET last_harvest_at=?, last_harvest_error=? WHERE id=?",
                (now, "无雪球 ID", pid),
            )
            conn.commit()
            continue
        if i and person_gap_s:
            sleep(person_gap_s)
            if mono() >= deadline:
                stopped = True
                break
        people += 1
        person_new = 0
        err = ""
        oldest = ""
        statuses: list = []
        try:
            try:
                statuses = fetch_st(uid, n) or []
            except RateLimited as exc:
                err = f"触发限流，今日停: {exc}"
                logger.warning("大V限流停采 %s: %s", uid, exc)
                statuses = []
                stopped = True
            except Exception as exc:
                err = f"主页失败: {type(exc).__name__}"
                logger.warning("大V主页失败 %s: %s", uid, exc)
                statuses = []
            for st in statuses:
                if cap and person_new >= cap:
                    break
                if mono() >= deadline:
                    stopped = True
                    err = err or "预算耗尽，已续跑"
                    break
                url = str(st.get("url") or "").strip()
                if not url:
                    continue
                exist = conn.execute(
                    "SELECT 1 FROM big_v_opinion WHERE url=? LIMIT 1", (url,)
                ).fetchone()
                if exist:
                    day_seen = _norm_day(st.get("time") or "", day0)
                    if day_seen and (not oldest or day_seen < oldest):
                        oldest = day_seen
                    continue
                art = None
                if _timeline_enough(st):
                    art = _status_article(st, day0)
                else:
                    try:
                        art = fetch_art(url)
                    except RateLimited as exc:
                        err = f"触发限流，今日停: {exc}"
                        logger.warning("大V正文限流 %s: %s", url[:60], exc)
                        stopped = True
                        break
                    except Exception as exc:
                        logger.warning("大V正文失败 %s: %s", url[:60], exc)
                        art = None
                    if gap_s:
                        extra = roll.uniform(0, max(0.0, gap_jitter_s))
                        sleep(gap_s + extra)
                    if not art:
                        fallback = _status_article(st, day0)
                        art = fallback if fallback.get("text") else None
                    if not art:
                        err = err or "正文抓取失败"
                        continue
                text = (art.get("text") or "").strip()
                if not text:
                    err = err or "正文为空"
                    continue
                day = _norm_day(art.get("time") or st.get("time") or "", day0)
                if day and (not oldest or day < oldest):
                    oldest = day
                r = insert_opinion(
                    conn, pid,
                    url=art.get("url") or url,
                    title=art.get("title") or st.get("title") or "",
                    text=text,
                    opinion_date=day,
                )
                if r.get("inserted"):
                    person_new += 1
        except RateLimited as exc:
            err = err or f"触发限流，今日停: {exc}"
            logger.warning("大V采集限流 %s: %s", uid, exc)
            stopped = True
        except Exception as exc:
            err = err or f"采集失败: {type(exc).__name__}"
            logger.warning("大V采集失败 %s: %s", uid, exc)
        cur = oldest or prof.get("backfill_cursor") or ""
        done = 1 if (backfill and not stopped and len(statuses) < n) else 0
        conn.execute(
            """UPDATE big_v_profile
               SET backfill_cursor=?,
                   backfill_done=CASE WHEN ?=1 THEN 1 ELSE backfill_done END
               WHERE id=?""",
            (cur, done, pid),
        )
        conn.execute(
            """UPDATE big_v_profile
               SET last_harvest_at=?, last_harvest_new=?, last_harvest_error=?
               WHERE id=?""",
            (now, person_new, err, pid),
        )
        conn.commit()
        total_new += person_new
        if err:
            errors.append(f"{prof.get('name')}:{err}")
        if stopped:
            break

    return {
        "new": total_new,
        "people": people,
        "stopped_budget": stopped,
        "errors": errors,
    }
