"""大V画像库对抗审查：采集 / FTS / 问答 / 路由真 bug。临时库，全 mock。"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db

ASOF = "2026-09-08"


def _tmp_db():
    fd, p = tempfile.mkstemp(suffix="_bigv_adv.db")
    os.close(fd)
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _close(conn, path):
    conn.close()
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(path + s)
        except OSError:
            pass


def test_grounded_generic_zenme_does_not_cite_unrelated_maotai():
    """有据：问比特币不能因为原文有「怎么走」就当命中并编立场。"""
    from invest.bigv.ask import ask_big_v
    from invest.bigv.persist import insert_opinion
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="段永平", xueqiu="111")
        insert_opinion(
            conn, "xq_111",
            url="https://xueqiu.com/111/a",
            title="茅台",
            text="长期看茅台商业模式很好。今天怎么走都是长期持有。",
            opinion_date=ASOF,
        )
        called = {"n": 0}

        def complete(system, user):
            called["n"] += 1
            return "他支持比特币"

        out = ask_big_v(
            conn, name="段永平", question="怎么看比特币", mode="grounded",
            complete_fn=complete,
        )
        assert out["ok"] is True
        assert out.get("empty_reason") == "no_hit"
        assert called["n"] == 0
        assert "他支持比特币" not in out["answer"]
        assert not out.get("citations")
    finally:
        _close(conn, p)


def test_fts_isolated_top8_does_not_leak_other_profile():
    """FTS/检索按 profile_id 隔离，最多 8 条，不串到另一人。"""
    from invest.bigv.persist import insert_opinion
    from invest.bigv.search import search_opinions
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="段永平", xueqiu="111")
        register(conn, name="林园", xueqiu="222")
        for i in range(10):
            insert_opinion(
                conn, "xq_111",
                url=f"https://xueqiu.com/111/{i}",
                title="茅台",
                text=f"段永平谈茅台第{i}篇，商业模式很好。",
                opinion_date=ASOF,
            )
        for i in range(4):
            insert_opinion(
                conn, "xq_222",
                url=f"https://xueqiu.com/222/{i}",
                title="茅台",
                text=f"林园说茅台不要碰第{i}篇。",
                opinion_date=ASOF,
            )
        hits = search_opinions(conn, "xq_111", "茅台怎么看")
        assert 1 <= len(hits) <= 8
        blobs = " ".join((h.get("body") or "") + (h.get("url") or "") for h in hits)
        assert "不要碰" not in blobs
        assert "xueqiu.com/222/" not in blobs
        hits_b = search_opinions(conn, "xq_222", "茅台怎么看")
        assert hits_b and all("不要碰" in (h.get("body") or "") for h in hits_b)
        assert all("xueqiu.com/111/" not in (h.get("url") or "") for h in hits_b)
    finally:
        _close(conn, p)


def test_resolve_does_not_guess_longer_name():
    """名单只有「段永平笔记」时，点名「段永平」不得当成就是他。"""
    from invest.bigv.watch import register, resolve_name

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="段永平笔记", xueqiu="999")
        r = resolve_name(conn, name="段永平")
        assert r["status"] != "ok"
        assert r["status"] in ("ambiguous", "missing")
    finally:
        _close(conn, p)


def test_persona_refreshes_when_new_article_arrives():
    """有新全文必须重压人格卡，不能只等 7 天。"""
    from invest.bigv.persist import insert_opinion
    from invest.bigv.persona import maybe_refresh_persona
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="段永平", xueqiu="111")
        insert_opinion(
            conn, "xq_111",
            url="https://xueqiu.com/111/old",
            title="旧",
            text="只买好生意。",
            opinion_date="2026-09-01",
        )
        calls = {"n": 0}

        def complete(system, user):
            calls["n"] += 1
            return f"卡{calls['n']}"

        r1 = maybe_refresh_persona(conn, "xq_111", complete_fn=complete, now="2026-09-08")
        assert r1["refreshed"] is True and calls["n"] == 1
        insert_opinion(
            conn, "xq_111",
            url="https://xueqiu.com/111/new",
            title="新",
            text="新文专门谈白酒。",
            opinion_date="2026-09-08",
        )
        r2 = maybe_refresh_persona(conn, "xq_111", complete_fn=complete, now="2026-09-08")
        assert r2["refreshed"] is True and calls["n"] == 2
        card = conn.execute(
            "SELECT persona_card FROM big_v_profile WHERE id=?", ("xq_111",)
        ).fetchone()["persona_card"]
        assert "卡2" in card
    finally:
        _close(conn, p)


def test_backfill_budget_stop_does_not_mark_done():
    """回灌时间预算到点必须可续，不能因本批条数<N 就标 backfill_done。"""
    from invest.bigv.harvest import harvest
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="回灌", xueqiu="888")
        clock = {"t": 0.0}

        def fetch_st(uid, limit=15):
            return [{"url": f"https://xueqiu.com/888/{i}", "title": "x"} for i in range(5)]

        def fetch_art(url):
            clock["t"] += 10
            return {"url": url, "title": "x", "time": "2026-01-01", "text": "正文"}

        out = harvest(
            conn,
            fetch_statuses=fetch_st,
            fetch_article=fetch_art,
            backfill=True,
            limit=30,
            budget_s=15,
            monotonic=lambda: clock["t"],
            sleep_fn=lambda _s: None,
        )
        assert out["stopped_budget"] is True
        assert out["new"] >= 1
        row = conn.execute(
            "SELECT backfill_done, last_harvest_error FROM big_v_profile WHERE id=?",
            ("xq_888",),
        ).fetchone()
        assert row["backfill_done"] != 1
        assert row["last_harvest_error"]
    finally:
        _close(conn, p)


def test_harvest_skips_empty_body():
    """没抓到全文不得当成功入库（view 有标题、body 空）。"""
    from invest.bigv.harvest import harvest
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="空文", xueqiu="1010")

        def fetch_st(uid, limit=15):
            return [{"url": "https://xueqiu.com/1010/empty", "title": "只有标题"}]

        def fetch_art(url):
            return {"url": url, "title": "只有标题", "time": ASOF, "text": ""}

        out = harvest(
            conn, fetch_statuses=fetch_st, fetch_article=fetch_art,
            sleep_fn=lambda _s: None,
        )
        n = conn.execute(
            "SELECT COUNT(*) FROM big_v_opinion WHERE profile_id=?", ("xq_1010",)
        ).fetchone()[0]
        assert n == 0
        assert out["new"] == 0
        err = conn.execute(
            "SELECT last_harvest_error FROM big_v_profile WHERE id=?", ("xq_1010",)
        ).fetchone()["last_harvest_error"]
        assert err
    finally:
        _close(conn, p)


def test_ask_strips_buy_advice():
    """回答是人物视角，不得留下「建议买入」。"""
    from invest.bigv.ask import ask_big_v
    from invest.bigv.persist import insert_opinion
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="段永平", xueqiu="111")
        insert_opinion(
            conn, "xq_111",
            url="https://xueqiu.com/111/mt",
            title="茅台",
            text="茅台还可以继续持有。",
            opinion_date=ASOF,
        )
        out = ask_big_v(
            conn, name="段永平", question="茅台怎么看", mode="grounded",
            complete_fn=lambda s, u: "建议买入茅台",
        )
        assert out["ok"] is True
        assert "建议买入" not in out["answer"]
    finally:
        _close(conn, p)


def test_harvest_one_person_insert_failure_does_not_stop_others():
    """单人入库异常不得中断名单里后面的人。"""
    from invest.bigv.harvest import harvest
    from invest.bigv.persist import insert_opinion
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="甲炸", xueqiu="666")
        register(conn, name="乙好", xueqiu="777")

        def boom(conn_, profile_id, **kw):
            if profile_id == "xq_666":
                raise RuntimeError("insert boom")
            return insert_opinion(conn_, profile_id, **kw)

        def fetch_st(uid, limit=15):
            return [{"url": f"https://xueqiu.com/{uid}/z", "title": "t"}]

        def fetch_art(url):
            return {"url": url, "title": "t", "time": ASOF, "text": "全文"}

        with patch("invest.bigv.harvest.insert_opinion", boom):
            out = harvest(
                conn, fetch_statuses=fetch_st, fetch_article=fetch_art,
                sleep_fn=lambda _s: None,
            )
        n777 = conn.execute(
            "SELECT COUNT(*) FROM big_v_opinion WHERE profile_id=?", ("xq_777",)
        ).fetchone()[0]
        assert n777 == 1
        assert out["new"] >= 1
        err666 = conn.execute(
            "SELECT last_harvest_error FROM big_v_profile WHERE id=?", ("xq_666",)
        ).fetchone()["last_harvest_error"]
        assert err666
    finally:
        _close(conn, p)


def test_harvest_budget_resume_fetches_remaining_person():
    """预算到点停下后，下次应跳过已入库 URL 并采到下一人。"""
    from invest.bigv.harvest import harvest
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="慢A", xueqiu="1313")
        register(conn, name="慢B", xueqiu="1414")
        clock = {"t": 0.0}
        seen: list[str] = []

        def fetch_st(uid, limit=15):
            return [{"url": f"https://xueqiu.com/{uid}/1", "title": "t"}]

        def fetch_art(url):
            seen.append(url)
            clock["t"] += 20
            return {"url": url, "title": "t", "time": ASOF, "text": "全文"}

        out1 = harvest(
            conn, fetch_statuses=fetch_st, fetch_article=fetch_art,
            budget_s=15, monotonic=lambda: clock["t"], sleep_fn=lambda _s: None,
        )
        assert out1["stopped_budget"] is True
        first = list(seen)
        assert len(first) == 1
        clock["t"] = 0.0
        seen.clear()
        out2 = harvest(
            conn, fetch_statuses=fetch_st, fetch_article=fetch_art,
            budget_s=15, monotonic=lambda: clock["t"], sleep_fn=lambda _s: None,
        )
        assert out2["new"] == 1
        assert seen == ["https://xueqiu.com/1414/1"] or seen[0].endswith("/1414/1")
        n = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT profile_id, COUNT(*) FROM big_v_opinion "
                "WHERE profile_id IN ('xq_1313','xq_1414') GROUP BY profile_id"
            )
        }
        assert n.get("xq_1313") == 1 and n.get("xq_1414") == 1
    finally:
        _close(conn, p)


def test_command_unknown_lists_roster_nl_unknown_is_none():
    """/大V 名单外必须失败提示；口语未点中返回 None。"""
    from invest.bigv.route import try_feishu
    from invest.bigv.watch import register

    p = _tmp_db()
    conn = connect(p)
    try:
        register(conn, name="段永平", xueqiu="111")
        cmd = try_feishu(conn, "/大V 路人甲 茅台怎么看", complete_fn=lambda s, u: "x")
        assert cmd is not None and "名单里没有" in cmd
        assert "段永平" in cmd
        nl = try_feishu(conn, "问路人甲 茅台怎么看", complete_fn=lambda s, u: "x")
        assert nl is None
        infer = try_feishu(
            conn, "/大V 段永平 推断一下半导体",
            complete_fn=lambda s, u: "按风格会先看生意。",
        )
        assert infer is not None and "推断" in infer
    finally:
        _close(conn, p)
