"""大V画像库：名单/采集/FTS/问答/飞书分流。临时库，全 mock，不联网。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db

ASOF = "2026-09-08"


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_bigv_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _register(conn, name="段永平", xueqiu="6192813830"):
    from invest.bigv.watch import register

    return register(conn, name=name, xueqiu=xueqiu)


def test_schema_watched_body_fts():
    from invest.db import SCHEMA_VERSION

    p = _tmp_db()
    conn = connect(p)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver >= 21
        assert ver == SCHEMA_VERSION or ver >= 21
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(big_v_profile)")}
        for c in ("watched", "persona_card", "persona_updated_at", "last_harvest_at",
                  "last_harvest_new", "last_harvest_error", "backfill_cursor", "backfill_done"):
            assert c in pcols
        ocols = {r[1] for r in conn.execute("PRAGMA table_info(big_v_opinion)")}
        assert "body" in ocols
        fts = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='big_v_opinion_fts'"
        ).fetchone()
        assert fts is not None
    finally:
        conn.close()


def test_register_parses_url_and_only_watched():
    from invest.bigv.watch import list_watched, parse_xueqiu_id, set_watched

    assert parse_xueqiu_id("https://xueqiu.com/u/6192813830") == "6192813830"
    assert parse_xueqiu_id("https://xueqiu.com/6192813830") == "6192813830"
    assert parse_xueqiu_id("6192813830") == "6192813830"
    assert parse_xueqiu_id("bad") is None

    p = _tmp_db()
    conn = connect(p)
    try:
        r = _register(conn, "段永平", "https://xueqiu.com/u/6192813830")
        assert r["ok"] is True and r["profile_id"] == "xq_6192813830"
        row = conn.execute("SELECT * FROM big_v_profile WHERE id=?", ("xq_6192813830",)).fetchone()
        assert row["watched"] == 1 and row["name"] == "段永平"
        r2 = _register(conn, "林园", "12345")
        assert r2["ok"]
        set_watched(conn, "xq_12345", 0)
        watched = list_watched(conn)
        assert [w["id"] for w in watched] == ["xq_6192813830"]
    finally:
        conn.close()


def test_preserve_watched_on_profile_replace():
    from invest.bigv.watch import preserve_profile_fields

    p = _tmp_db()
    conn = connect(p)
    try:
        _register(conn, "段永平", "6192813830")
        conn.execute(
            "UPDATE big_v_profile SET persona_card='卡' WHERE id=?",
            ("xq_6192813830",),
        )
        conn.commit()
        row = preserve_profile_fields(conn, {
            "id": "xq_6192813830", "name": "段永平", "platform": "xueqiu",
        })
        assert row["watched"] == 1 and row["persona_card"] == "卡"
    finally:
        conn.close()


def test_persist_dedup_and_fts_hit():
    from invest.bigv.persist import insert_opinion
    from invest.bigv.search import search_opinions

    p = _tmp_db()
    conn = connect(p)
    try:
        _register(conn)
        pid = "xq_6192813830"
        a = insert_opinion(
            conn, pid,
            url="https://xueqiu.com/6192813830/1",
            title="茅台还要拿着",
            text="长期看茅台商业模式很好，不要因为波动就卖。",
            opinion_date=ASOF,
        )
        assert a["inserted"] is True
        b = insert_opinion(
            conn, pid,
            url="https://xueqiu.com/6192813830/1",
            title="重复",
            text="另一篇",
            opinion_date=ASOF,
        )
        assert b["inserted"] is False
        n = conn.execute("SELECT COUNT(*) FROM big_v_opinion WHERE profile_id=?", (pid,)).fetchone()[0]
        assert n == 1
        body = conn.execute("SELECT body, view FROM big_v_opinion WHERE url=?",
                            ("https://xueqiu.com/6192813830/1",)).fetchone()
        assert "商业模式" in (body["body"] or "")
        assert len(body["view"] or "") <= 80
        hits = search_opinions(conn, pid, "茅台怎么看")
        assert hits and "茅台" in (hits[0]["body"] or hits[0]["topic"] or "")
    finally:
        conn.close()


def test_harvest_watched_only_and_budget():
    from invest.bigv.harvest import harvest
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="段永平", xueqiu="111")
        register(conn, name="路人", xueqiu="222")
        conn.execute("UPDATE big_v_profile SET watched=0 WHERE id=?", ("xq_222",))
        conn.commit()

        statuses = {
            "111": [{"url": "https://xueqiu.com/111/a", "title": "看多白酒"}],
            "222": [{"url": "https://xueqiu.com/222/z", "title": "不该采"}],
        }

        def fetch_st(uid, limit=15):
            return list(statuses.get(str(uid), []))[:limit]

        def fetch_art(url):
            return {"url": url, "title": "看多白酒", "time": ASOF, "text": "茅台还可以持有。"}

        clock = {"t": 0.0}

        def mono():
            return clock["t"]

        out = harvest(
            conn,
            fetch_statuses=fetch_st,
            fetch_article=fetch_art,
            limit=15,
            budget_s=900,
            monotonic=mono,
            sleep_fn=lambda _s: None,
        )
        assert out["new"] == 1
        urls = [r[0] for r in conn.execute("SELECT url FROM big_v_opinion").fetchall()]
        assert urls == ["https://xueqiu.com/111/a"]
        row = conn.execute("SELECT last_harvest_new, last_harvest_error FROM big_v_profile WHERE id=?",
                           ("xq_111",)).fetchone()
        assert row["last_harvest_new"] == 1
        assert not row["last_harvest_error"]

        register(conn, name="慢人", xueqiu="333")
        statuses["333"] = [{"url": f"https://xueqiu.com/333/{i}", "title": "x"} for i in range(5)]

        def fetch_art2(url):
            clock["t"] += 10
            return {"url": url, "title": "x", "time": ASOF, "text": "正文"}

        clock["t"] = 0.0
        out2 = harvest(
            conn,
            fetch_statuses=fetch_st,
            fetch_article=fetch_art2,
            limit=15,
            budget_s=15,
            monotonic=mono,
            sleep_fn=lambda _s: None,
        )
        assert out2["stopped_budget"] is True
    finally:
        conn.close()


def test_waf_text_ignores_generic_verify_word():
    from invest.data.xueqiu_fetch import _is_waf_text

    assert _is_waf_text("登录 注册 手机验证 热门话题") is False
    assert _is_waf_text("访问过于频繁，请稍后再试") is True
    assert _is_waf_text("请完成验证后继续访问") is True


def test_harvest_skips_article_uses_timeline_and_caps():
    """已入库不打开正文；时间线够长不打开正文；每人每日封顶。"""
    from invest.bigv.harvest import harvest
    from invest.bigv.persist import insert_opinion
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="段永平", xueqiu="111")
        insert_opinion(
            conn, "xq_111",
            url="https://xueqiu.com/111/old",
            title="旧文",
            text="已经入库的旧观点。",
            opinion_date=ASOF,
        )
        opened: list[str] = []

        def fetch_st(uid, limit=15):
            return [
                {"url": "https://xueqiu.com/111/old", "title": "旧文", "text": "不该再抓"},
                {"url": "https://xueqiu.com/111/long", "title": "长帖",
                 "time": "昨天 21:00",
                 "text": "时间线已经有足够长的原文，茅台还可以继续拿着不必追高，也不用因为波动就卖出核心仓。"},
                {"url": "https://xueqiu.com/111/short", "title": "短帖",
                 "time": ASOF, "text": "展开"},
                {"url": "https://xueqiu.com/111/extra", "title": "超额",
                 "time": ASOF, "text": "这是第三条新帖，超出每人封顶就不该入库。"},
            ][:limit]

        def fetch_art(url):
            opened.append(url)
            return {"url": url, "title": "短帖", "time": ASOF, "text": "打开正文后的补充内容。"}

        out = harvest(
            conn,
            fetch_statuses=fetch_st,
            fetch_article=fetch_art,
            max_new_per_person=2,
            sleep_fn=lambda _s: None,
            today=dt.date(2026, 9, 10),
        )
        assert out["new"] == 2
        assert "https://xueqiu.com/111/old" not in opened
        assert "https://xueqiu.com/111/long" not in opened
        assert opened == ["https://xueqiu.com/111/short"]
        urls = {r[0] for r in conn.execute("SELECT url FROM big_v_opinion").fetchall()}
        assert "https://xueqiu.com/111/long" in urls
        assert "https://xueqiu.com/111/short" in urls
        assert "https://xueqiu.com/111/extra" not in urls
        day = conn.execute(
            "SELECT opinion_date FROM big_v_opinion WHERE url=?",
            ("https://xueqiu.com/111/long",),
        ).fetchone()[0]
        assert day == "2026-09-09"
        row = conn.execute(
            "SELECT backfill_cursor, last_harvest_new FROM big_v_profile WHERE id=?",
            ("xq_111",),
        ).fetchone()
        assert row["last_harvest_new"] == 2
        assert row["backfill_cursor"]
    finally:
        conn.close()


def test_harvest_rate_limit_stops_all():
    from invest.bigv.harvest import RateLimited, harvest
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="甲", xueqiu="111")
        register(conn, name="乙", xueqiu="222")

        seen: list[str] = []

        def fetch_st(uid, limit=15):
            seen.append(str(uid))
            raise RateLimited("访问过于频繁")

        out = harvest(
            conn,
            fetch_statuses=fetch_st,
            fetch_article=lambda url: None,
            sleep_fn=lambda _s: None,
        )
        assert out["stopped_budget"] is True
        assert out["new"] == 0
        assert out["people"] == 1
        assert len(seen) == 1
        errs = [
            r[0] for r in conn.execute("SELECT last_harvest_error FROM big_v_profile")
        ]
        assert any(e and "限流" in e for e in errs)
    finally:
        conn.close()


def test_ask_grounded_and_infer():
    from invest.bigv.ask import ask_big_v
    from invest.bigv.persist import insert_opinion

    p = _tmp_db()
    conn = connect(p)
    try:
        _register(conn)
        insert_opinion(
            conn, "xq_6192813830",
            url="https://xueqiu.com/6192813830/mt",
            title="茅台",
            text="茅台还可以继续持有，别追高也不要吓跑。",
            opinion_date=ASOF,
        )
        miss = ask_big_v(conn, name="段永平", question="比特币杠杆怎么加", mode="grounded",
                         complete_fn=lambda s, u: "我不该出现")
        assert miss["ok"] is True
        assert miss["empty_reason"] == "no_hit"
        assert "没" in miss["answer"]
        assert "我不该出现" not in miss["answer"]

        hit = ask_big_v(
            conn, name="段永平", question="茅台还要不要拿", mode="grounded",
            complete_fn=lambda s, u: "按原文，还可以持有。",
        )
        assert hit["ok"] is True and hit["citations"]
        assert "2026-09-08" in hit["answer"] or hit["citations"][0]["date"] == ASOF
        assert "xueqiu.com" in (hit["citations"][0].get("url") or "")

        inf = ask_big_v(
            conn, name="段永平", question="新能源车怎么看", mode="infer",
            complete_fn=lambda s, u: "按他的风格会先看生意。",
        )
        assert inf["ok"] is True
        assert "推断" in inf["answer"]

        miss_name = ask_big_v(conn, name="不存在的人", question="你好", mode="grounded")
        assert miss_name["ok"] is False and "名单里没有" in miss_name["error"]
    finally:
        conn.close()


def test_feishu_route_command_and_nl():
    from invest.bigv.persist import insert_opinion
    from invest.bigv.route import parse_feishu, try_feishu

    p = _tmp_db()
    conn = connect(p)
    try:
        _register(conn)
        insert_opinion(
            conn, "xq_6192813830",
            url="https://xueqiu.com/6192813830/mt",
            title="茅台",
            text="茅台生意很好。",
            opinion_date=ASOF,
        )
        cmd = parse_feishu("/大V 段永平 茅台怎么看")
        assert cmd and cmd["kind"] == "command" and cmd["mode"] == "grounded"
        inf = parse_feishu("/大V 段永平 推断一下茅台")
        assert inf and inf["mode"] == "infer"
        nl = parse_feishu("问段永平：茅台怎么看")
        assert nl and nl["kind"] == "nl" and nl["name"] == "段永平"
        assert parse_feishu("今天半导体怎么样") is None

        ans = try_feishu(conn, "/大V 路人甲 随便问问",
                         complete_fn=lambda s, u: "x")
        assert ans is not None and "名单里没有" in ans

        none = try_feishu(conn, "问路人甲 茅台怎么看",
                          complete_fn=lambda s, u: "x")
        assert none is None

        hit = try_feishu(conn, "以段永平的视角 茅台怎么看",
                         complete_fn=lambda s, u: "生意还行。")
        assert hit and "生意" in hit

        ambig = _register(conn, "段永平", "999")
        assert ambig["ok"]
        q = try_feishu(conn, "/大V 段永平 茅台", complete_fn=lambda s, u: "x")
        assert q and "重名" in q
    finally:
        conn.close()


def test_persona_refresh_on_new_text():
    from invest.bigv.persist import insert_opinion
    from invest.bigv.persona import maybe_refresh_persona

    p = _tmp_db()
    conn = connect(p)
    try:
        _register(conn)
        insert_opinion(
            conn, "xq_6192813830",
            url="https://xueqiu.com/6192813830/p",
            title="生意",
            text="只买看得懂的好生意。",
            opinion_date=ASOF,
        )
        calls = {"n": 0}

        def complete(system, user):
            calls["n"] += 1
            return '{"voice":"慢","stance":"好生意"}'

        r1 = maybe_refresh_persona(
            conn, "xq_6192813830", complete_fn=complete, now="2026-09-08",
        )
        assert r1["refreshed"] is True and calls["n"] == 1
        card = conn.execute("SELECT persona_card FROM big_v_profile WHERE id=?",
                            ("xq_6192813830",)).fetchone()["persona_card"]
        assert "好生意" in card
        last = conn.execute(
            "SELECT persona_updated_at FROM big_v_profile WHERE id=?",
            ("xq_6192813830",),
        ).fetchone()["persona_updated_at"]
        from datetime import date, timedelta
        d0 = date.fromisoformat(last[:10])
        r2 = maybe_refresh_persona(
            conn, "xq_6192813830", complete_fn=complete,
            now=(d0 + timedelta(days=1)).isoformat(),
        )
        assert r2["refreshed"] is False and calls["n"] == 1
        r3 = maybe_refresh_persona(
            conn, "xq_6192813830", complete_fn=complete,
            now=(d0 + timedelta(days=7)).isoformat(),
        )
        assert r3["refreshed"] is True and calls["n"] == 2
    finally:
        conn.close()


if __name__ == "__main__":
    test_schema_watched_body_fts()
    test_register_parses_url_and_only_watched()
    test_preserve_watched_on_profile_replace()
    test_persist_dedup_and_fts_hit()
    test_harvest_watched_only_and_budget()
    test_waf_text_ignores_generic_verify_word()
    test_harvest_skips_article_uses_timeline_and_caps()
    test_harvest_rate_limit_stops_all()
    test_ask_grounded_and_infer()
    test_feishu_route_command_and_nl()
    test_persona_refresh_on_new_text()
    print("test_bigv OK")
