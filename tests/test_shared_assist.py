"""共享层对抗审查：调度槽位 / schema / 新鲜度守卫 / 工具写库。

不连真实网络，不读 data/invest.db。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import SCHEMA_VERSION, connect, init_db
from invest.scheduler import JOB_FUNCS

ROOT = Path(__file__).resolve().parents[1]


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(p)
    return p


def test_schema_version_is_21_with_horizon_actions_and_fts():
    assert SCHEMA_VERSION == 21
    p = _tmp_db()
    conn = connect(p)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 21
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # 2026-09-18：review_lessons（复盘校验库）已删
        assert {"daily_actions", "watch_items", "trade_signals"} <= tables
        assert "review_lessons" not in tables
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_signals)")}
        assert {"horizon", "layer"} <= cols
        fts = conn.execute(
            "SELECT name FROM sqlite_master WHERE name='big_v_opinion_fts'"
        ).fetchone()
        assert fts is not None
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(big_v_profile)")}
        assert {"watched", "persona_card", "backfill_cursor", "body"} <= pcols | {
            r[1] for r in conn.execute("PRAGMA table_info(big_v_opinion)")
        }
        assert "watched" in pcols
    finally:
        conn.close()
        os.remove(p)


# 2026-09-18：action_digest / action_digest_pm（10:00 / 13:30 动作 digest）已按需求删除，
# 原 test_action_digest_am_and_pm_are_separate_slots 一并移除。


def test_os_manifest_digest_jobs_not_collapsed():
    """PS1 里同一 job 不得映射到两个时刻（findall 字典会把后一个覆盖前一个）。"""
    raw = (ROOT / "scripts" / "install_os_tasks.ps1").read_text(encoding="utf-8-sig")
    entries = re.findall(r'Time = "(\d\d:\d\d)"; Job = "([^"]+)"', raw)
    times_by_job: dict[str, str] = {}
    for time, job in entries:
        if job in times_by_job and times_by_job[job] != time:
            pytest.fail(f"Job={job} 同时映射 {times_by_job[job]} 与 {time}；一天跑两次必须拆两个 job 名")
        times_by_job[job] = time
    assert set(times_by_job) == set(JOB_FUNCS)


def test_weekend_no_second_xueqiu_harvest():
    """周日 20:00 不再二次打雪球，避免和工作日/每日 17:10 叠打被限流。"""
    import inspect

    from invest.scheduler import _weekend

    src = inspect.getsource(_weekend)
    assert "bigv.harvest" not in src
    assert "_bv_harvest" not in src


def test_daily_harvest_backfills_slowly_every_day():
    import datetime as dt
    import inspect

    from invest.scheduler import TRADING_DAY_JOBS, _big_v_harvest, _job_is_scheduled_today

    src = inspect.getsource(_big_v_harvest)
    assert "backfill=True" in src
    assert "max_new_per_person" in src
    assert "big_v_harvest" not in TRADING_DAY_JOBS
    assert _job_is_scheduled_today("big_v_harvest", dt.date(2026, 9, 10)) is True
    assert _job_is_scheduled_today("big_v_harvest", dt.date(2026, 9, 12)) is True


def test_big_v_harvest_refreshes_stale_persona_without_new(monkeypatch):
    """7 天到期重压不能依赖 last_harvest_new>0，否则没新文的人永远不刷新。"""
    import importlib

    from invest.scheduler import _big_v_harvest

    called: list[tuple[str, bool]] = []
    hmod = importlib.import_module("invest.bigv.harvest")
    wmod = importlib.import_module("invest.bigv.watch")
    pmod = importlib.import_module("invest.bigv.persona")
    monkeypatch.setattr(hmod, "harvest", lambda *a, **k: {"new": 0, "errors": []})
    monkeypatch.setattr(
        wmod, "list_watched", lambda conn: [{"id": "xq_1", "last_harvest_new": 0}],
    )
    monkeypatch.setattr(
        pmod,
        "maybe_refresh_persona",
        lambda conn, pid, force=False: called.append((pid, force)),
    )
    p = _tmp_db()
    conn = connect(p)
    try:
        r = _big_v_harvest(p, conn)
        assert r.status == "ok"
        assert called == [("xq_1", False)]
    finally:
        conn.close()
        os.remove(p)


def test_d32_and_d33_skip_freshness_gate():
    from invest.agent.tools import _SECTION_NO_GATE

    assert "d32_trade_signals" in _SECTION_NO_GATE
    assert "d33_daily_actions" in _SECTION_NO_GATE


def test_big_v_update_opinion_writes_body_into_fts():
    from invest.agent.tools import big_v_update
    from invest.bigv.search import search_opinions

    p = _tmp_db()
    conn = connect(p)
    try:
        r = big_v_update(conn, action="upsert_profile", name="段永平", xueqiu_id="123")
        assert r["ok"]
        pid = r["profile_id"]
        r = big_v_update(
            conn,
            action="upsert_opinion",
            profile_id=pid,
            view="摘要",
            body="全文里有茅台护城河三个字",
            url="https://xueqiu.com/n/1",
        )
        assert r["ok"]
        stored = conn.execute("SELECT body FROM big_v_opinion WHERE id=?", (r["opinion_id"],)).fetchone()
        assert stored["body"] == "全文里有茅台护城河三个字"
        hits = search_opinions(conn, pid, "茅台怎么看")
        assert hits and "茅台" in (hits[0].get("body") or ""), "粘贴全文必须能被有据检索命中"
        r2 = big_v_update(
            conn,
            action="upsert_opinion",
            profile_id=pid,
            view="重复",
            url="https://xueqiu.com/n/1",
        )
        assert r2["ok"]
        n = conn.execute("SELECT COUNT(*) FROM big_v_opinion").fetchone()[0]
        assert n == 1, "同一 URL 不得再插一行"
    finally:
        conn.close()
        os.remove(p)
