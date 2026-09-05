from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from invest.db import connect, init_db
from invest.scheduler import (
    JobExecutionError,
    JobResult,
    _claim_execution,
    _finish_execution,
    _in_auction_window,
    _intraday_tick_job,
    run_job_once,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "invest.db")
    init_db(path)
    return path


def _execution(db_path: str):
    conn = connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM job_executions WHERE job='auction' AND scheduled_date='2026-08-24'"
        ).fetchone()
    finally:
        conn.close()


def test_job_execution_schema_has_unique_slot_and_delivery_fields(db_path: str):
    conn = connect(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_executions)")}
        assert {
            "job", "scheduled_date", "run_slot", "status", "attempt", "detail",
            "artifact", "channel_results", "started_at", "finished_at", "updated_at",
            "lease_expires_at", "lease_owner",
        } <= columns
        receipt_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(delivery_receipts)")
        }
        assert {"message_kind", "message_id"} <= receipt_columns
        conn.execute(
            """INSERT INTO job_executions(job, scheduled_date, run_slot, status, attempt)
               VALUES('auction', '2026-08-24', '09:26', 'running', 1)"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO job_executions(job, scheduled_date, run_slot, status, attempt)
                   VALUES('auction', '2026-08-24', '09:26', 'running', 1)"""
            )
    finally:
        conn.close()


def test_v15_receipt_migration_preserves_formal_message_identity(tmp_path: Path):
    db_path = str(tmp_path / "v15.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """CREATE TABLE delivery_receipts (
                   job TEXT NOT NULL, scheduled_date TEXT NOT NULL,
                   run_slot TEXT NOT NULL, channel TEXT NOT NULL,
                   status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                   detail TEXT DEFAULT '', started_at TEXT, succeeded_at TEXT,
                   updated_at TEXT,
                   PRIMARY KEY (job, scheduled_date, run_slot, channel)
               );
               INSERT INTO delivery_receipts(
                   job, scheduled_date, run_slot, channel, status, attempt
               ) VALUES('auction', '2026-08-24', '09:26', 'feishu', 'succeeded', 1);
               PRAGMA user_version=15;"""
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)
    migrated = connect(db_path)
    try:
        row = migrated.execute(
            """SELECT message_kind, message_id, status FROM delivery_receipts
               WHERE job='auction' AND channel='feishu'"""
        ).fetchone()
    finally:
        migrated.close()
    assert tuple(row) == ("report", "a7_auction", "succeeded")


def test_auction_false_retries_then_success_is_idempotent(db_path: str):
    now = dt.datetime(2026, 8, 24, 9, 26)
    with mock.patch("invest.pipeline.notify_auction", side_effect=[False, False, True]) as send:
        for expected_attempt in (1, 2):
            with pytest.raises(JobExecutionError):
                run_job_once("auction", db_path=db_path, now=now)
            row = _execution(db_path)
            assert row["status"] == "failed"
            assert row["attempt"] == expected_attempt

        result = run_job_once("auction", db_path=db_path, now=now)
        assert result.status == "ok"
        assert result.channel_results == {"delivery": "succeeded"}
        assert _execution(db_path)["attempt"] == 3

        duplicate = run_job_once("auction", db_path=db_path, now=now)
        assert duplicate.status == "already_ok"
        assert send.call_count == 3
        assert _execution(db_path)["attempt"] == 3


def test_running_slot_is_not_delivered_concurrently(db_path: str):
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO job_executions(
                       job, scheduled_date, run_slot, status, attempt, started_at,
                       updated_at, lease_expires_at
                   ) VALUES('auction', '2026-08-24', '09:26', 'running', 1,
                            datetime('now','localtime'), datetime('now','localtime'),
                            '2999-01-01 00:00:00')"""
            )
    finally:
        conn.close()

    with mock.patch("invest.pipeline.notify_auction") as send:
        result = run_job_once(
            "auction",
            db_path=db_path,
            now=dt.datetime(2026, 8, 24, 9, 26),
        )
    assert result.status == "already_running"
    send.assert_not_called()
    assert _execution(db_path)["attempt"] == 1


def test_expired_running_lease_is_reclaimed_within_auction_window(db_path: str):
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO job_executions(
                       job, scheduled_date, run_slot, status, attempt, started_at,
                       updated_at, lease_expires_at
                   ) VALUES('auction', '2026-08-24', '09:26', 'running', 1,
                            '2000-01-01 00:00:00', '2000-01-01 00:00:00',
                            '2000-01-01 00:01:00')"""
            )
    finally:
        conn.close()

    channels = {"feishu": True, "wecom": False, "weixin": False}
    with mock.patch("invest.pipeline.notify_auction", return_value=channels) as send:
        result = run_job_once(
            "auction",
            db_path=db_path,
            now=dt.datetime(2026, 8, 24, 9, 28),
        )
    assert result.status == "ok"
    assert result.channel_results == {
        "feishu": "succeeded",
        "wecom": "failed",
        "weixin": "failed",
    }
    send.assert_called_once()
    assert _execution(db_path)["attempt"] == 2
    assert _execution(db_path)["lease_expires_at"] is None


def test_expired_lease_owner_cannot_overwrite_reclaimed_attempt(db_path: str):
    conn = connect(db_path)
    try:
        claimed1, _, owner1 = _claim_execution(
            conn, "auction", "2026-08-24", "09:26", 60
        )
        assert claimed1 is True
        with conn:
            conn.execute(
                """UPDATE job_executions SET lease_expires_at='2000-01-01 00:00:00'
                   WHERE job='auction' AND scheduled_date='2026-08-24'"""
            )
        claimed2, _, owner2 = _claim_execution(
            conn, "auction", "2026-08-24", "09:26", 60
        )
        assert claimed2 is True
        assert owner1 != owner2

        assert _finish_execution(
            conn,
            "auction",
            "2026-08-24",
            "09:26",
            JobResult.ok("stale worker"),
            owner1,
        ) is False
        row = _execution(db_path)
        assert row["status"] == "running"
        assert row["attempt"] == 2
        assert row["lease_owner"] == owner2
    finally:
        conn.close()


def test_heartbeat_prevents_reclaim_after_initial_lease_expires(db_path: str):
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}
    outcome: list[JobResult] = []

    def slow_auction(_db, _conn):
        calls["n"] += 1
        started.set()
        assert release.wait(5)
        return JobResult.ok(
            "sent",
            channel_results={"feishu": True, "wecom": False, "weixin": False},
        )

    now = dt.datetime(2026, 8, 24, 9, 26)

    def run_slow():
        outcome.append(run_job_once(
            "auction",
            db_path=db_path,
            now=now,
            lease_seconds=1,
            heartbeat_interval=0.1,
        ))

    with mock.patch.dict("invest.scheduler.JOB_FUNCS", {"auction": slow_auction}):
        worker = threading.Thread(target=run_slow)
        worker.start()
        assert started.wait(2)
        first_lease = _execution(db_path)["lease_expires_at"]
        initial_expiry = dt.datetime.fromisoformat(first_lease)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            row = _execution(db_path)
            if (
                dt.datetime.now() > initial_expiry
                and dt.datetime.fromisoformat(row["lease_expires_at"]) > dt.datetime.now()
            ):
                break
            time.sleep(0.05)
        else:
            pytest.fail("heartbeat 未在初始租约过期后续租")

        concurrent = run_job_once(
            "auction",
            db_path=db_path,
            now=now,
            lease_seconds=1,
            heartbeat_interval=0.1,
        )
        assert concurrent.status == "already_running"
        assert calls["n"] == 1
        release.set()
        worker.join(3)

    assert not worker.is_alive()
    assert outcome[0].status == "ok"
    assert _execution(db_path)["attempt"] == 1


def test_ticker_auction_path_leaves_persistent_trace(db_path: str):
    now = dt.datetime(2026, 8, 24, 9, 26)
    with mock.patch("invest.pipeline.notify_auction", return_value=True):
        result = _intraday_tick_job(db_path=db_path, now=now)
    assert result.status == "ok"
    row = _execution(db_path)
    assert row["status"] == "ok"
    assert row["run_slot"] == "09:26"


def test_ticker_detects_missed_auction_once_without_backfill(db_path: str):
    late = dt.datetime(2026, 8, 24, 18, 31)
    with mock.patch("invest.data.calendar.is_trading_day", return_value=True), \
         mock.patch("invest.intraday._in_trading_window", return_value=False), \
         mock.patch("invest.pipeline.notify_auction") as send, \
         mock.patch("invest.scheduler.Notifier") as notifier:
        notifier.return_value.send_text.return_value = {
            "feishu": False,
            "wecom": True,
            "weixin": False,
        }
        first = _intraday_tick_job(db_path=db_path, now=late)
        second = _intraday_tick_job(db_path=db_path, now=late)
    assert first.status == "missed"
    assert second.status == "skipped"
    send.assert_not_called()
    notifier.return_value.send_text.assert_called_once()
    assert _execution(db_path)["attempt"] == 1
    assert json.loads(_execution(db_path)["channel_results"]) == {
        "alert/auction_missed/warning": "succeeded",
    }


def test_intraday_alerts_with_zero_delivery_is_failed_and_logged(db_path: str):
    alerts = [{"symbol": "000001", "change": 0.05}]
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO job_executions(
                       job, scheduled_date, run_slot, status, attempt
                   ) VALUES('auction', '2026-08-24', '09:26', 'missed', 1)"""
            )
    finally:
        conn.close()
    with mock.patch("invest.data.calendar.is_trading_day", return_value=True), \
         mock.patch("invest.intraday._in_trading_window", return_value=True), \
         mock.patch("invest.scheduler._tick_collect_pools"), \
         mock.patch("invest.monitor.run_p0_monitor"), \
         mock.patch("invest.intraday.check_core_moves", return_value=alerts), \
         mock.patch("invest.intraday.send_alerts", return_value=0):
        result = _intraday_tick_job(
            db_path=db_path,
            now=dt.datetime(2026, 8, 24, 10, 30),
        )
    assert result.status == "failed"
    assert result.channel_results == {"delivery": "failed"}
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, detail FROM job_runs WHERE job='intraday' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["status"] == "failed"
        assert "推送 0" in row["detail"]
    finally:
        conn.close()


def test_run_job_false_propagates_and_keeps_legacy_job_runs(db_path: str):
    now = dt.datetime(2026, 8, 24, 9, 26)
    with mock.patch("invest.pipeline.notify_auction", return_value=False), \
         pytest.raises(JobExecutionError):
        run_job_once("auction", db_path=db_path, now=now)
    conn = connect(db_path)
    try:
        latest = conn.execute(
            "SELECT status, detail FROM job_runs WHERE job='auction' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert latest["status"] == "failed"
        assert "投递" in latest["detail"]
    finally:
        conn.close()


def test_auction_uses_calendar_and_does_not_backfill_after_window(db_path: str):
    holiday = dt.datetime(2026, 8, 24, 9, 26)
    with mock.patch("invest.data.calendar.is_trading_day", return_value=False):
        assert _in_auction_window(holiday) is False

    late = dt.datetime(2026, 8, 24, 9, 31)
    with mock.patch("invest.data.calendar.is_trading_day", return_value=True), \
         mock.patch("invest.pipeline.notify_auction") as send, \
         mock.patch("invest.scheduler.Notifier") as notifier:
        result = run_job_once("auction", db_path=db_path, now=late)
    assert result.status == "missed"
    assert result.success is False
    send.assert_not_called()
    notifier.return_value.send_text.assert_called_once()
    assert "过窗" in _execution(db_path)["detail"]


def test_market_job_uses_trading_calendar_instead_of_weekday(db_path: str):
    with mock.patch("invest.data.calendar.is_trading_day", return_value=False), \
         mock.patch("invest.scheduler._snapshot_close") as task, \
         mock.patch.dict("invest.scheduler.JOB_FUNCS", {"snapshot_close": task}):
        result = run_job_once(
            "snapshot_close",
            db_path=db_path,
            now=dt.datetime(2026, 10, 1, 15, 1),
        )
    assert result.status == "skipped"
    assert "非交易日" in result.detail
    task.assert_not_called()


def test_os_task_manifest_matches_job_funcs_and_required_times():
    from invest.scheduler import JOB_FUNCS

    raw = (ROOT / "scripts" / "install_os_tasks.ps1").read_text(encoding="utf-8-sig")
    entries = re.findall(r'Time = "(\d\d:\d\d)"; Job = "([^"]+)"', raw)
    jobs_to_times = {job: time for time, job in entries}
    assert set(jobs_to_times) == set(JOB_FUNCS)
    assert jobs_to_times["auction"] == "09:26"
    assert jobs_to_times["snapshot_close"] == "15:01"
    assert jobs_to_times["pool_trap_scan"] == "17:10"
    assert (ROOT / "scripts" / "install_os_tasks.ps1").read_bytes().startswith(b"\xef\xbb\xbf")


def test_job_result_rejects_failed_cli_semantics():
    result = JobResult.failed("push failed")
    assert result.success is False


def test_run_job_cli_returns_nonzero_for_unsuccessful_result(monkeypatch: pytest.MonkeyPatch):
    from scripts import run_job

    monkeypatch.setattr(run_job.sys, "argv", ["run_job.py", "auction"])
    runner = mock.Mock(return_value=JobResult("missed", "过窗"))
    monkeypatch.setattr(run_job, "run_job_once", runner)
    assert run_job.main() == 1
    runner.assert_called_once_with("auction", wait_for_running=30.0)


def test_auction_artifact_is_persisted_only_after_delivery(db_path: str):
    from invest import pipeline
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    live = QuoteResult(
        ref=parse_asset("000001", "index"),
        price=3900.0, pct=0.003, status="live",
        freshness="unknown", fallback_level="none", src="tencent",
    )
    snap = ReportSnapshot(
        skill_id="a7_auction",
        as_of="2026-08-28T09:25:00",
        blocks={
            "index_quotes": DataBlock(
                "index_quotes", "2026-08-28T09:25:00", True,
                payload=[live], quotes=[live],
            ),
        },
    )
    struct = {
        "title": "竞价",
        "sections": [{"type": "text", "text": "竞价正文"}],
        "views": {"mood": {"state": "强"}},
    }
    with mock.patch("invest.skills.snapshot.freeze_snapshot", return_value=snap), \
         mock.patch("invest.skills.reports.a7_auction.render", return_value=struct), \
         mock.patch.object(pipeline, "_send_structured", return_value=False), \
         mock.patch.object(pipeline, "_persist_auction_views") as persist:
        assert pipeline.notify_auction(db_path) is False
        persist.assert_not_called()

    with mock.patch("invest.skills.snapshot.freeze_snapshot", return_value=snap), \
         mock.patch("invest.skills.reports.a7_auction.render", return_value=struct), \
         mock.patch.object(pipeline, "_send_structured", return_value=True), \
         mock.patch.object(pipeline, "_persist_auction_views") as persist:
        assert pipeline.notify_auction(db_path) is True
        persist.assert_called_once_with(struct["views"], db_path=db_path)


def test_notifier_and_structured_delivery_can_return_channel_results():
    from invest import pipeline
    from invest.notifier import Notifier

    notifier = Notifier(webhook="https://qyapi.weixin.qq.com/fake")
    with mock.patch.object(notifier, "_send_wecom", return_value=True), \
         mock.patch.object(notifier, "_send_feishu", return_value=False), \
         mock.patch.object(notifier, "_send_weixin", return_value=True):
        assert notifier.send_text("x", return_results=True) == {
            "wecom": True,
            "feishu": False,
            "weixin": True,
        }
        assert notifier.send_text("x") is True

    struct = {"title": "竞价", "sections": []}
    settings = mock.Mock(feishu_chat_id="oc_test")
    with mock.patch("invest.config.get_settings", return_value=settings), \
         mock.patch("invest.push.feishu_push.send_card", return_value=True), \
         mock.patch("invest.notifier.Notifier.send_text",
                    return_value={"wecom": True, "weixin": False}):
        assert pipeline._send_structured(struct, "auction", return_results=True) == {
            "feishu": True,
            "wecom": True,
            "weixin": False,
        }


def test_recurring_report_jobs_return_per_channel_results(db_path: str):
    from invest import scheduler

    channels = {"wecom": True, "feishu": False, "weixin": True}
    conn = connect(db_path)
    try:
        with mock.patch("invest.review.monthly.monthly_review", return_value={}), \
             mock.patch("invest.review.yearly.yearly_review", return_value={}), \
             mock.patch("invest.review.report.save_report"), \
             mock.patch("invest.skills.runner.run", return_value="report"), \
             mock.patch("invest.scheduler.Notifier") as notifier:
            notifier.return_value.send_text.return_value = channels
            monthly = scheduler._monthly(db_path, conn)
            yearly = scheduler._yearly(db_path, conn)
    finally:
        conn.close()

    assert monthly.status == "ok"
    expected_states = {
        "wecom": "succeeded",
        "feishu": "failed",
        "weixin": "succeeded",
    }
    assert monthly.channel_results == expected_states
    assert yearly.status == "ok"
    assert yearly.channel_results == expected_states


def test_weekend_and_pool_alert_return_per_channel_results(db_path: str):
    from invest import pipeline, scheduler

    channels = {"wecom": False, "feishu": True, "weixin": False}
    with mock.patch("invest.skills.runner.run", return_value="weekly"), \
         mock.patch("invest.notifier.Notifier.send_text", return_value=channels):
        assert pipeline.notify_weekend(
            db_path,
            "agent",
            return_results=True,
        ) == channels
        assert pipeline.notify_weekend(db_path, "agent") is True

    review = {
        "period": "2026-W35",
        "cards_review": [],
        "score": 90,
        "rogue_trades": 0,
        "cycle_drift": 0,
    }
    conn = connect(db_path)
    try:
        with mock.patch("invest.pipeline.collect"), \
             mock.patch("invest.pipeline.quant"), \
             mock.patch("invest.pipeline.notify_weekend", return_value=channels), \
             mock.patch("invest.review.weekly.weekly_review", return_value=review), \
             mock.patch("invest.review.report.save_report"):
            weekend = scheduler._weekend(db_path, conn)
    finally:
        conn.close()
    assert weekend.status == "ok"
    assert weekend.channel_results == {
        "wecom": "failed",
        "feishu": "succeeded",
        "weixin": "failed",
    }

    alert = {
        "symbol": "000001",
        "name": "平安银行",
        "level": "🟡",
        "trap_score": 50,
        "signals_hit": [{"name": "测试", "evidence": "e"}],
        "recommendation": "observe",
    }
    conn = connect(db_path)
    try:
        with mock.patch(
            "invest.skills.sections.d31_pool_trap_alerts.scan_pool",
            return_value=[alert],
        ), mock.patch("invest.scheduler.Notifier") as notifier:
            notifier.return_value.send_text.return_value = channels
            pool = scheduler._pool_trap_scan(db_path, conn)
    finally:
        conn.close()
    assert pool.status == "ok"
    assert pool.channel_results == {
        "wecom": "failed",
        "feishu": "succeeded",
        "weixin": "failed",
    }


def test_terminal_auction_ticks_initialize_database_only_once(db_path: str):
    from invest import scheduler

    scheduler._DB_INIT_CACHE.clear()
    real_init = scheduler.init_db
    late = dt.datetime(2026, 8, 24, 18, 31)
    with mock.patch("invest.scheduler.init_db", wraps=real_init) as init, \
         mock.patch("invest.data.calendar.is_trading_day", return_value=True), \
         mock.patch("invest.scheduler.Notifier") as notifier:
        notifier.return_value.send_text.return_value = {"wecom": True}
        _intraday_tick_job(db_path=db_path, now=late)
        _intraday_tick_job(db_path=db_path, now=late)
        _intraday_tick_job(db_path=db_path, now=late)
    assert init.call_count == 1


def test_uncertain_outbox_receipt_is_not_blindly_resent(db_path: str):
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO delivery_receipts(
                       job, scheduled_date, run_slot, message_kind, message_id,
                       channel, status, attempt
                   ) VALUES('auction', '2026-08-24', '09:26',
                            'report', 'a7_auction', 'feishu', 'sending', 1)"""
            )
    finally:
        conn.close()

    struct = {"title": "竞价", "sections": [], "views": {}}
    settings = mock.Mock(
        feishu_chat_id="oc_test",
        wecom_webhook="https://qyapi.weixin.qq.com/fake",
    )
    with mock.patch("invest.skills.runner.run_structured", return_value=struct), \
         mock.patch("invest.config.get_settings", return_value=settings), \
         mock.patch("invest.notifier.get_settings", return_value=settings), \
         mock.patch("invest.push.feishu_push.send_card") as feishu, \
         mock.patch("invest.notifier.Notifier._send_wecom", return_value=True), \
         mock.patch("invest.notifier.Notifier._send_weixin", return_value=True), \
         pytest.raises(JobExecutionError):
        run_job_once(
            "auction",
            db_path=db_path,
            now=dt.datetime(2026, 8, 24, 9, 26),
        )
    feishu.assert_not_called()
    conn = connect(db_path)
    try:
        receipt = conn.execute(
            """SELECT status, attempt FROM delivery_receipts
               WHERE job='auction' AND channel='feishu'"""
        ).fetchone()
        execution = conn.execute(
            """SELECT status, detail FROM job_executions
               WHERE job='auction' AND scheduled_date='2026-08-24'"""
        ).fetchone()
    finally:
        conn.close()
    assert receipt["status"] == "uncertain"
    assert receipt["attempt"] == 1
    assert execution["status"] == "failed"
    assert "人工核验" in execution["detail"]


def test_outbox_retries_only_failed_channels_without_process_throttle(db_path: str):
    from invest.delivery import delivery_context
    from invest.notifier import _LAST_SEND, Notifier

    _LAST_SEND.clear()
    notifier = Notifier(webhook="https://qyapi.weixin.qq.com/fake")
    with mock.patch.object(notifier, "_send_wecom", return_value=True) as wecom, \
         mock.patch.object(
             notifier,
             "_send_weixin",
             side_effect=[False, True],
         ) as weixin:
        with delivery_context(db_path, "weekend", "2026-08-24", "20:00"):
            first = notifier.send_text(
                "weekly",
                key="weekend",
                min_interval=600,
                feishu=False,
                return_results=True,
            )
        with delivery_context(db_path, "weekend", "2026-08-24", "20:00"):
            second = notifier.send_text(
                "weekly",
                key="weekend",
                min_interval=600,
                feishu=False,
                return_results=True,
            )
    assert first == {"wecom": True, "weixin": False}
    assert second == {"wecom": True, "weixin": True}
    assert wecom.call_count == 1
    assert weixin.call_count == 2


def test_run_job_cli_uses_tempfail_for_deferred_and_nonzero_for_final_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import run_job

    monkeypatch.setattr(run_job.sys, "argv", ["run_job.py", "auction"])
    runner = mock.Mock(return_value=JobResult("deferred", "同槽等待超时"))
    monkeypatch.setattr(run_job, "run_job_once", runner)
    assert run_job.main() == 75
    runner.assert_called_once_with("auction", wait_for_running=30.0)

    runner.return_value = JobResult.failed("并发任务最终失败")
    assert run_job.main() == 1

    runner.return_value = JobResult("skipped", "非交易日")
    assert run_job.main() == 1

    runner.return_value = JobResult("already_ok", "已观察到最终成功")
    assert run_job.main() == 0


def test_core_jobs_reject_implicit_success_and_validate_snapshot_counts(db_path: str):
    from invest import scheduler

    assert scheduler._normalize_result(None).status == "failed"
    assert scheduler._normalize_result({"anything": 1}).status == "failed"

    conn = connect(db_path)
    try:
        with mock.patch(
            "invest.pipeline.snapshot_close",
            return_value={"stock": 0, "market": 0, "index": 0},
        ):
            failed = scheduler._snapshot_close(db_path, conn)
        with mock.patch(
            "invest.pipeline.snapshot_close",
            return_value={"stock": 1, "market": 5200, "index": 3},
        ):
            succeeded = scheduler._snapshot_close(db_path, conn)
    finally:
        conn.close()
    assert failed.status == "failed"
    assert succeeded.status == "ok"
    assert "market=5200" in succeeded.detail


def test_premarket_all_collection_failures_are_not_ok(db_path: str):
    from invest import scheduler

    conn = connect(db_path)
    try:
        with mock.patch(
            "invest.pipeline.collect",
            return_value=[
                {"name": "daily", "status": "failed"},
                {"name": "index", "status": "failed"},
            ],
        ), mock.patch("invest.pipeline.quant") as quant, \
             mock.patch("invest.pipeline.agent_premarket") as agent:
            result = scheduler._premarket(db_path, conn)
    finally:
        conn.close()
    assert result.status == "failed"
    quant.assert_not_called()
    agent.assert_not_called()


def test_delivery_receipts_distinguish_message_purposes(db_path: str):
    from invest.delivery import deliver_channel, delivery_context

    calls = {"n": 0}

    def sent():
        calls["n"] += 1
        return True

    with delivery_context(db_path, "auction", "2026-08-24", "09:26"):
        assert deliver_channel(
            "feishu",
            sent,
            message_kind="report",
            message_id="a7_auction",
        ) is True
        assert deliver_channel(
            "feishu",
            sent,
            message_kind="alert",
            message_id="auction_missed",
        ) is True
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """SELECT message_kind, message_id, status FROM delivery_receipts
               WHERE job='auction' ORDER BY message_kind"""
        ).fetchall()
    finally:
        conn.close()
    assert calls["n"] == 2
    assert {
        (row["message_kind"], row["message_id"], row["status"]) for row in rows
    } == {
        ("report", "a7_auction", "succeeded"),
        ("alert", "auction_missed", "succeeded"),
    }


def test_delivery_outbox_distinguishes_failed_from_uncertain(db_path: str):
    import requests

    from invest.delivery import deliver_channel, delivery_context

    failed_calls = {"n": 0}
    uncertain_calls = {"n": 0}

    def rejected():
        failed_calls["n"] += 1
        return failed_calls["n"] > 1

    def timeout():
        uncertain_calls["n"] += 1
        raise requests.Timeout("read timed out")

    for _ in range(2):
        with delivery_context(db_path, "weekend", "2026-08-24", "20:00"):
            deliver_channel(
                "wecom",
                rejected,
                message_kind="report",
                message_id="a4_weekly",
            )
            deliver_channel(
                "feishu",
                timeout,
                message_kind="report",
                message_id="a4_weekly",
            )
    conn = connect(db_path)
    try:
        states = {
            row["channel"]: row["status"]
            for row in conn.execute(
                """SELECT channel, status FROM delivery_receipts
                   WHERE job='weekend'"""
            )
        }
    finally:
        conn.close()
    assert states == {"wecom": "succeeded", "feishu": "uncertain"}
    assert failed_calls["n"] == 2
    assert uncertain_calls["n"] == 1


def test_notifier_network_timeout_is_persisted_as_uncertain(db_path: str):
    import requests

    from invest.delivery import delivery_context
    from invest.notifier import Notifier

    notifier = Notifier(webhook="https://qyapi.weixin.qq.com/fake")
    with mock.patch(
        "requests.Session.post",
        side_effect=requests.Timeout("read timed out"),
    ), mock.patch.object(
        notifier,
        "_send_weixin",
        return_value=False,
    ), delivery_context(db_path, "weekend", "2026-08-24", "20:00"):
        result = notifier.send_text(
            "weekly",
            feishu=False,
            return_results=True,
            message_kind="report",
            message_id="a4_weekly",
        )
    assert result["wecom"] is False
    conn = connect(db_path)
    try:
        status = conn.execute(
            """SELECT status FROM delivery_receipts
               WHERE job='weekend' AND message_kind='report'
                 AND message_id='a4_weekly' AND channel='wecom'"""
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "uncertain"


def test_feishu_timeout_in_outbox_is_uncertain_without_internal_resend(db_path: str):
    import requests

    from invest.delivery import deliver_channel, delivery_context
    from invest.push import feishu_push

    session = mock.Mock()
    session.post.side_effect = requests.ConnectionError("connection reset")
    with mock.patch.object(feishu_push, "_tenant_token", return_value="token"), \
         mock.patch.object(feishu_push, "_session", return_value=session), \
         delivery_context(db_path, "auction", "2026-08-24", "09:26"):
        result = deliver_channel(
            "feishu",
            lambda: feishu_push.send_message("oc_test", "chat_id", "auction"),
            message_kind="report",
            message_id="a7_auction",
        )
    assert result is False
    assert session.post.call_count == 1
    conn = connect(db_path)
    try:
        status = conn.execute(
            """SELECT status FROM delivery_receipts
               WHERE job='auction' AND message_id='a7_auction'
                 AND channel='feishu'"""
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "uncertain"


def test_weixin_disconnect_in_outbox_is_uncertain(db_path: str):
    import urllib.error

    from invest.delivery import delivery_context
    from invest.notifier import Notifier
    from invest.push import weixin_push

    settings = mock.Mock(weixin_token="token", weixin_to_user_id="user")
    opener = mock.Mock()
    opener.open.side_effect = urllib.error.URLError("connection reset")
    notifier = Notifier(webhook="https://qyapi.weixin.qq.com/fake")
    with mock.patch.object(weixin_push, "get_settings", return_value=settings), \
         mock.patch("urllib.request.build_opener", return_value=opener), \
         mock.patch.object(notifier, "_send_wecom", return_value=False), \
         delivery_context(db_path, "weekend", "2026-08-24", "20:00"):
        result = notifier.send_text(
            "weekly",
            feishu=False,
            return_results=True,
            message_kind="report",
            message_id="a4_weekly",
        )
    assert result["weixin"] is False
    conn = connect(db_path)
    try:
        status = conn.execute(
            """SELECT status FROM delivery_receipts
               WHERE job='weekend' AND message_id='a4_weekly'
                 AND channel='weixin'"""
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "uncertain"


def test_daily_refresh_requires_main_bars_and_benchmark_index(db_path: str):
    from invest import scheduler

    summary = [
        {"name": "daily_bars", "status": "failed"},
        {"name": "index_bars", "status": "failed"},
        {"name": "index_bars_000905", "status": "ok"},
    ]
    conn = connect(db_path)
    try:
        with mock.patch(
            "invest.pipeline.collect_bars_and_indices",
            return_value=summary,
        ), mock.patch("invest.pipeline.quant") as quant:
            result = scheduler._daily_refresh(db_path, conn)
    finally:
        conn.close()
    assert result.status == "failed"
    assert "daily_bars" in result.detail
    assert "index_bars" in result.detail
    quant.assert_not_called()


@pytest.mark.parametrize(
    ("job_name", "now"),
    [
        ("premarket", dt.datetime(2026, 8, 24, 8, 35)),
        ("auction", dt.datetime(2026, 8, 24, 9, 27)),
        ("snapshot_close", dt.datetime(2026, 8, 24, 15, 30)),
        ("evening_report", dt.datetime(2026, 8, 24, 22, 15)),
    ],
)
def test_compensation_scan_retries_due_jobs_inside_business_window(
    db_path: str,
    job_name: str,
    now: dt.datetime,
):
    from invest import scheduler

    task = mock.Mock(return_value=JobResult.ok(f"{job_name} recovered"))
    with mock.patch.dict(scheduler.JOB_FUNCS, {job_name: task}, clear=True), \
         mock.patch("invest.data.calendar.is_trading_day", return_value=True):
        first = scheduler.run_compensation_scan(
            db_path=db_path,
            now=now,
            jobs={job_name},
        )
        second = scheduler.run_compensation_scan(
            db_path=db_path,
            now=now,
            jobs={job_name},
        )
    assert first[job_name].status == "ok"
    assert second[job_name].status == "already_ok"
    task.assert_called_once()


def test_compensation_scan_marks_auction_missed_without_late_report(db_path: str):
    from invest import scheduler

    task = mock.Mock(return_value=JobResult.ok("must not run"))
    now = dt.datetime(2026, 8, 24, 10, 0)
    with mock.patch.dict(scheduler.JOB_FUNCS, {"auction": task}, clear=True), \
         mock.patch("invest.data.calendar.is_trading_day", return_value=True), \
         mock.patch("invest.scheduler.Notifier") as notifier:
        notifier.return_value.send_text.return_value = {"wecom": True}
        result = scheduler.run_compensation_scan(
            db_path=db_path,
            now=now,
            jobs={"auction"},
        )
    assert result["auction"].status == "missed"
    task.assert_not_called()
    notifier.return_value.send_text.assert_called_once()
    assert _execution(db_path)["status"] == "missed"


def test_compensation_scan_marks_generic_overdue_job_missed(db_path: str):
    from invest import scheduler

    task = mock.Mock(return_value=JobResult.ok("must not run"))
    now = dt.datetime(2026, 8, 24, 10, 0)
    with mock.patch.dict(scheduler.JOB_FUNCS, {"premarket": task}, clear=True), \
         mock.patch("invest.data.calendar.is_trading_day", return_value=True), \
         mock.patch("invest.scheduler.Notifier") as notifier:
        notifier.return_value.send_text.return_value = {"wecom": True}
        result = scheduler.run_compensation_scan(
            db_path=db_path,
            now=now,
            jobs={"premarket"},
        )
    assert result["premarket"].status == "missed"
    task.assert_not_called()


def test_compensation_scan_retries_previous_failure_in_same_window(db_path: str):
    from invest import scheduler

    now = dt.datetime(2026, 8, 24, 15, 30)
    task = mock.Mock(
        side_effect=[
            JobResult.failed("temporary failure"),
            JobResult.ok("snapshot recovered"),
        ]
    )
    with mock.patch.dict(
        scheduler.JOB_FUNCS,
        {"snapshot_close": task},
        clear=True,
    ), mock.patch("invest.data.calendar.is_trading_day", return_value=True):
        first = scheduler.run_compensation_scan(
            db_path=db_path,
            now=now,
            jobs={"snapshot_close"},
        )
        second = scheduler.run_compensation_scan(
            db_path=db_path,
            now=now,
            jobs={"snapshot_close"},
        )
    assert first["snapshot_close"].status == "failed"
    assert second["snapshot_close"].status == "ok"
    conn = connect(db_path)
    try:
        row = conn.execute(
            """SELECT status, attempt FROM job_executions
               WHERE job='snapshot_close'"""
        ).fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("ok", 2)


def test_compensation_scan_uses_trading_calendar(db_path: str):
    from invest import scheduler

    task = mock.Mock(return_value=JobResult.ok("must not run"))
    with mock.patch.dict(
        scheduler.JOB_FUNCS,
        {"snapshot_close": task},
        clear=True,
    ), mock.patch("invest.data.calendar.is_trading_day", return_value=False):
        result = scheduler.run_compensation_scan(
            db_path=db_path,
            now=dt.datetime(2026, 10, 1, 15, 30),
            jobs={"snapshot_close"},
        )
    assert result == {}
    task.assert_not_called()
