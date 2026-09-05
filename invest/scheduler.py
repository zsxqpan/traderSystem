"""调度器：盘前/盘后/周末/夜间例行任务（APScheduler）。

盘中实时行情通道（2026-08-18 决策）：独立 ticker 每 10 秒轮询核心池，
非交易时段由 _in_trading_window 守护（空转无副作用）；
异动推送与失败留痕照常，正常轮询不写 job_runs（留痕由
log_realtime_health 节流承担，正常 60s 一条基线、异常立即记）。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from invest.db import connect, init_db
from invest.notifier import Notifier

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class JobResult:
    """任务的明确业务结果；只有 ok 才会封闭该计划槽位。"""

    status: str
    detail: str = ""
    artifact: str = ""
    channel_results: dict[str, str] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status in {"ok", "already_ok", "skipped"}

    @classmethod
    def ok(
        cls,
        detail: str = "",
        *,
        artifact: str = "",
        channel_results: dict[str, str] | None = None,
    ) -> JobResult:
        return cls("ok", detail, artifact, channel_results or {})

    @classmethod
    def failed(
        cls,
        detail: str,
        *,
        artifact: str = "",
        channel_results: dict[str, str] | None = None,
    ) -> JobResult:
        return cls("failed", detail, artifact, channel_results or {})


class JobExecutionError(RuntimeError):
    """业务失败或执行异常；OS 任务据此返回非零退出码。"""


JOB_SLOTS = {
    "premarket": "08:30",
    "morning_brief": "08:40",
    "auction": "09:26",
    "snapshot_close": "15:01",
    "after_close": "16:00",
    "pool_trap_scan": "17:10",
    "weekend": "20:00",
    "monthly": "09:30",
    "yearly": "09:30",
    "industry_refresh": "21:30",
    "daily_refresh": "21:40",
    "factcard_refresh": "21:50",
    "evening_report": "22:00",
}
# 同日补偿窗口。开始前不处理；窗口内复用正常执行链路；结束后只记 missed。
JOB_COMPENSATION_WINDOWS = {
    "premarket": (dt.time(8, 30), dt.time(8, 39, 59)),
    "morning_brief": (dt.time(8, 40), dt.time(9, 24, 59)),
    "auction": (dt.time(9, 25, 30), dt.time(9, 29, 30)),
    "snapshot_close": (dt.time(15, 1), dt.time(21, 39, 59)),
    "after_close": (dt.time(16, 0), dt.time(21, 29, 59)),
    "pool_trap_scan": (dt.time(17, 10), dt.time(21, 59, 59)),
    "weekend": (dt.time(20, 0), dt.time(23, 59, 59)),
    "monthly": (dt.time(9, 30), dt.time(23, 59, 59)),
    "yearly": (dt.time(9, 30), dt.time(23, 59, 59)),
    "industry_refresh": (dt.time(21, 30), dt.time(21, 39, 59)),
    "daily_refresh": (dt.time(21, 40), dt.time(21, 59, 59)),
    "factcard_refresh": (dt.time(21, 50), dt.time(21, 59, 59)),
    "evening_report": (dt.time(22, 0), dt.time(23, 59, 59)),
}
TRADING_DAY_JOBS = {
    "premarket",
    "morning_brief",
    "auction",
    "snapshot_close",
    "after_close",
    "pool_trap_scan",
    "industry_refresh",
    "daily_refresh",
    "factcard_refresh",
}
JOB_LEASE_SECONDS = {"auction": 180}
DEFAULT_JOB_LEASE_SECONDS = 7200
_DB_INIT_CACHE: dict[str, tuple[int, int]] = {}
_DB_INIT_LOCK = threading.Lock()


def _ensure_db_initialized(db_path: str) -> None:
    """每个数据库文件实例仅初始化一次；文件被替换后自动重新初始化。"""
    path = Path(db_path).resolve()
    key = str(path)
    try:
        stat = path.stat()
        signature = (stat.st_dev, stat.st_ino)
    except FileNotFoundError:
        signature = (-1, -1)
    if _DB_INIT_CACHE.get(key) == signature:
        return
    with _DB_INIT_LOCK:
        try:
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino)
        except FileNotFoundError:
            signature = (-1, -1)
        if _DB_INIT_CACHE.get(key) == signature:
            return
        init_db(key)
        stat = path.stat()
        _DB_INIT_CACHE[key] = (stat.st_dev, stat.st_ino)


def _log_run(conn, job: str, status: str, detail: str = "") -> None:
    with conn:
        conn.execute(
            """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
               VALUES(?, ?, datetime('now','localtime'), datetime('now','localtime'), ?)""",
            (job, status, detail),
        )


def _normalize_result(value: Any) -> JobResult:
    if isinstance(value, JobResult):
        return value
    return JobResult.failed(
        f"任务未明确返回 JobResult（实际 {type(value).__name__}）"
    )


def _collection_result(
    summary: Any,
    artifact: str,
    *,
    required: set[str] | None = None,
) -> JobResult:
    if not isinstance(summary, list):
        return JobResult.failed(
            "采集未返回任务摘要",
            artifact=artifact,
        )
    succeeded = [
        item for item in summary
        if isinstance(item, dict) and item.get("status") == "ok"
    ]
    if not succeeded:
        return JobResult.failed(
            f"采集全部失败（任务数={len(summary)}）",
            artifact=artifact,
        )
    if required:
        states = {
            str(item.get("name")): item.get("status")
            for item in summary
            if isinstance(item, dict)
        }
        missing = sorted(name for name in required if states.get(name) != "ok")
        if missing:
            return JobResult.failed(
                "必需采集产物失败: " + ",".join(missing),
                artifact=artifact,
            )
    return JobResult.ok(
        f"采集成功 {len(succeeded)}/{len(summary)}",
        artifact=artifact,
    )


def _delivery_result(
    value: bool | dict[str, bool],
    *,
    success_detail: str,
    failure_detail: str,
    artifact: str,
) -> JobResult:
    raw_channels = value if isinstance(value, dict) else {"delivery": bool(value)}
    channels = {
        channel: "succeeded" if succeeded else "failed"
        for channel, succeeded in raw_channels.items()
    }
    if "succeeded" not in channels.values():
        return JobResult.failed(
            failure_detail,
            artifact=artifact,
            channel_results=channels,
        )
    return JobResult.ok(
        success_detail,
        artifact=artifact,
        channel_results=channels,
    )


def _claim_execution(
    conn,
    job: str,
    scheduled_date: str,
    run_slot: str,
    lease_seconds: int,
) -> tuple[bool, str, str]:
    """原子占有计划槽位，防止 OS 任务与 ticker 同时投递。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """SELECT status, lease_expires_at FROM job_executions
               WHERE job=? AND scheduled_date=? AND run_slot=?""",
            (job, scheduled_date, run_slot),
        ).fetchone()
        status = str(row["status"]) if row else ""
        lease_active = bool(
            row
            and row["lease_expires_at"]
            and conn.execute(
                "SELECT ? > datetime('now','localtime')",
                (row["lease_expires_at"],),
            ).fetchone()[0]
        )
        if status in {"ok", "missed"} or (status == "running" and lease_active):
            conn.commit()
            return False, status, ""
        lease_modifier = f"+{lease_seconds} seconds"
        lease_owner = uuid.uuid4().hex
        if row:
            conn.execute(
                """UPDATE job_executions SET
                       status='running', attempt=attempt + 1, detail='', artifact='',
                       channel_results='{}', started_at=datetime('now','localtime'),
                       lease_expires_at=datetime('now','localtime', ?), lease_owner=?,
                       finished_at=NULL, updated_at=datetime('now','localtime')
                   WHERE job=? AND scheduled_date=? AND run_slot=?""",
                (lease_modifier, lease_owner, job, scheduled_date, run_slot),
            )
        else:
            conn.execute(
                """INSERT INTO job_executions(
                       job, scheduled_date, run_slot, status, attempt, started_at,
                       lease_expires_at, lease_owner, updated_at
                   ) VALUES(?, ?, ?, 'running', 1, datetime('now','localtime'),
                            datetime('now','localtime', ?), ?, datetime('now','localtime'))""",
                (job, scheduled_date, run_slot, lease_modifier, lease_owner),
            )
        conn.commit()
        return True, "running", lease_owner
    except Exception:
        conn.rollback()
        raise


def _finish_execution(
    conn,
    job: str,
    scheduled_date: str,
    run_slot: str,
    result: JobResult,
    lease_owner: str,
) -> bool:
    with conn:
        cursor = conn.execute(
            """UPDATE job_executions SET
                   status=?, detail=?, artifact=?, channel_results=?,
                   lease_expires_at=NULL, lease_owner=NULL,
                   finished_at=datetime('now','localtime'),
                   updated_at=datetime('now','localtime')
               WHERE job=? AND scheduled_date=? AND run_slot=? AND lease_owner=?""",
            (
                result.status,
                result.detail,
                result.artifact,
                json.dumps(result.channel_results, ensure_ascii=False, sort_keys=True),
                job,
                scheduled_date,
                run_slot,
                lease_owner,
            ),
        )
    if cursor.rowcount != 1:
        return False
    _log_run(conn, job, result.status, result.detail)
    return True


def _renew_execution_lease(
    db_path: str,
    job: str,
    scheduled_date: str,
    run_slot: str,
    lease_owner: str,
    lease_seconds: int,
) -> bool:
    """仅当前 owner 可续租；owner 被回收后旧 worker 无法复活租约。"""
    conn = connect(db_path)
    try:
        modifier = f"+{lease_seconds} seconds"
        with conn:
            cursor = conn.execute(
                """UPDATE job_executions SET
                       lease_expires_at=datetime('now','localtime', ?),
                       updated_at=datetime('now','localtime')
                   WHERE job=? AND scheduled_date=? AND run_slot=?
                     AND status='running' AND lease_owner=?""",
                (
                    modifier,
                    job,
                    scheduled_date,
                    run_slot,
                    lease_owner,
                ),
            )
        return cursor.rowcount == 1
    finally:
        conn.close()


class _LeaseHeartbeat:
    """后台定时续租；上下文退出时等待线程可靠停止。"""

    def __init__(
        self,
        db_path: str,
        job: str,
        scheduled_date: str,
        run_slot: str,
        lease_owner: str,
        lease_seconds: int,
        interval: float | None = None,
    ):
        self._args = (
            db_path,
            job,
            scheduled_date,
            run_slot,
            lease_owner,
            lease_seconds,
        )
        self._interval = interval or max(1.0, lease_seconds / 3)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"lease-heartbeat-{job}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if not _renew_execution_lease(*self._args):
                    return
            except Exception:
                logger.warning("任务租约续期失败: %s", self._args[1], exc_info=True)


def _notify_job_failure(job_name: str, detail: str) -> None:
    try:
        Notifier().send_text(f"任务 {job_name} 失败: {detail}", key=f"{job_name}_failed")
    except Exception:
        logger.warning("失败告警推送异常: %s", detail)


def _execute_job(
    job_name: str,
    fn: Callable,
    db: str,
    *,
    now: dt.datetime | None = None,
    lease_seconds: int | None = None,
    heartbeat_interval: float | None = None,
) -> JobResult:
    from invest.delivery import delivery_context

    now = now or dt.datetime.now()
    scheduled_date = now.date().isoformat()
    run_slot = JOB_SLOTS.get(job_name, "manual")
    _ensure_db_initialized(db)
    conn = connect(db)
    heartbeat = None
    try:
        effective_lease = lease_seconds or JOB_LEASE_SECONDS.get(
            job_name,
            DEFAULT_JOB_LEASE_SECONDS,
        )
        claimed, existing, lease_owner = _claim_execution(
            conn,
            job_name,
            scheduled_date,
            run_slot,
            effective_lease,
        )
        if not claimed and existing == "ok":
            return JobResult("already_ok", "该计划槽位已成功")
        if not claimed and job_name == "auction" and existing == "missed":
            return JobResult("already_missed", "竞价漏跑已记录")
        if not claimed and existing == "running":
            return JobResult("already_running", "该计划槽位正在执行")
        heartbeat = _LeaseHeartbeat(
            db,
            job_name,
            scheduled_date,
            run_slot,
            lease_owner,
            effective_lease,
            heartbeat_interval,
        )
        heartbeat.start()
        _log_run(conn, job_name, "running")

        if job_name in TRADING_DAY_JOBS:
            from invest.data.calendar import is_trading_day

            if not is_trading_day(now.date()):
                result = JobResult("skipped", "非交易日")
                _finish_execution(
                    conn, job_name, scheduled_date, run_slot, result, lease_owner
                )
                return result
        if job_name == "auction":
            if now.time() > dt.time(9, 29, 30):
                detail = "竞价投递已过窗，不使用盘中数据补发"
                try:
                    with delivery_context(
                        db,
                        job_name,
                        scheduled_date,
                        run_slot,
                    ) as warning_delivery:
                        raw_warning = Notifier().send_text(
                            f"⚠️ 任务 auction 漏跑：{detail}",
                            key=f"auction_missed_{scheduled_date}",
                            return_results=True,
                            message_kind="alert",
                            message_id="auction_missed",
                        )
                    channels = dict(warning_delivery.channel_states)
                    if not channels:
                        channels = {
                            "alert/auction_missed/warning": (
                                "succeeded"
                                if (
                                    any(raw_warning.values())
                                    if isinstance(raw_warning, dict)
                                    else bool(raw_warning)
                                )
                                else "failed"
                            )
                        }
                except Exception:
                    logger.warning("竞价漏跑告警推送异常")
                    channels = {"alert/auction_missed/warning": "uncertain"}
                result = JobResult("missed", detail, channel_results=channels)
                _finish_execution(
                    conn, job_name, scheduled_date, run_slot, result, lease_owner
                )
                return result
            if now.time() < dt.time(9, 25, 30):
                result = JobResult("skipped", "竞价投递窗口尚未开始")
                _finish_execution(
                    conn, job_name, scheduled_date, run_slot, result, lease_owner
                )
                return result

        try:
            with delivery_context(
                db,
                job_name,
                scheduled_date,
                run_slot,
            ) as deliveries:
                result = _normalize_result(fn(db, conn))
        except Exception as exc:
            result = JobResult.failed(str(exc))
            _finish_execution(
                conn, job_name, scheduled_date, run_slot, result, lease_owner
            )
            logger.exception("%s 失败", job_name)
            _notify_job_failure(job_name, result.detail)
            raise JobExecutionError(f"{job_name}: {result.detail}") from exc

        if deliveries.channel_states:
            result = JobResult(
                result.status,
                result.detail,
                result.artifact,
                dict(deliveries.channel_states),
            )
        if deliveries.uncertain_channels:
            result = JobResult.failed(
                "投递结果不确定，需人工核验: "
                + ",".join(sorted(deliveries.uncertain_channels)),
                artifact=result.artifact,
                channel_results=result.channel_results,
            )

        finished = _finish_execution(
            conn, job_name, scheduled_date, run_slot, result, lease_owner
        )
        if not finished:
            raise JobExecutionError(f"{job_name}: 执行租约已被回收，忽略过期结果")
        if not result.success:
            _notify_job_failure(job_name, result.detail)
            raise JobExecutionError(f"{job_name}: {result.detail}")
        return result
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        conn.close()


def _wrap(job_name: str, fn) -> Callable:
    def run() -> JobResult:
        db = str(ROOT / "data" / "invest.db")
        return _execute_job(job_name, fn, db)

    return run


def log_service_started() -> None:
    """服务启动时写一条 job_runs，便于确认调度器确实活着，并自检 webhook 可达性。"""
    db = str(ROOT / "data" / "invest.db")
    detail = "service started"
    try:
        import requests
        webhook = Notifier().webhook
        if webhook:
            last_err = ""
            for _attempt in range(3):  # 瞬时 TLS/网络抖动重试，避免误报不可达
                try:
                    r = requests.get(webhook, timeout=8)  # GET 只探测，不发送消息
                    if r.status_code == 200:
                        detail += " | webhook=http200"
                        break
                    last_err = f"http{r.status_code}"
                except Exception as exc:
                    last_err = f"{type(exc).__name__}"
                    time.sleep(3)
            else:
                detail += f" | webhook=unreachable({last_err})"
        else:
            detail += " | webhook=not_configured"
    except Exception as exc:
        detail += f" | webhook=check_failed({type(exc).__name__})"
    try:
        _ensure_db_initialized(db)
        conn = connect(db)
        try:
            _log_run(conn, "scheduler", "running", detail)
        finally:
            conn.close()
    except Exception:
        logger.exception("服务启动日志写入失败")


def _premarket(db: str, conn) -> JobResult:
    import invest.pipeline as pl
    collected = _collection_result(pl.collect(db), "premarket")
    if not collected.success:
        return collected
    pl.quant(db)
    # 环境重评触发检查（[B]8）：ERP 跨分位/社融拐点/10Y>20bp，触发即推送提示
    try:
        from invest.discipline.macro_gate import check_env_retrigger, env_retrigger_text
        result = check_env_retrigger(conn)
        text = env_retrigger_text(result)
        if text:
            _log_run(conn, "env_retrigger", "ok", f"触发 {result['n']} 条")
            Notifier().send_text(text, key="env_retrigger", min_interval=43200)
        else:
            _log_run(conn, "env_retrigger", "ok", "无触发")
    except Exception as exc:
        logger.warning("环境重评检查失败: %s", exc)
    text = pl.agent_premarket(db)
    # 2026-08-22：不再直接推送 A1；Agent 关注方向落盘，供 8:40 盘前报告(a0)「今日关注」节
    try:
        (ROOT / "data" / "premarket_agent.txt").write_text(text or "", encoding="utf-8")
    except Exception as exc:
        logger.warning("盘前 Agent 关注方向落盘失败: %s", exc)
    return collected


def _morning_brief(db: str, conn) -> JobResult:
    """盘前信息早报（2026-08-16）：8:40，关键信息简明扼要（8:30 采集后发）。"""
    import invest.pipeline as pl
    raw = pl.notify_morning_brief(db, return_results=True)
    return _delivery_result(
        raw,
        success_detail="盘前报告投递成功",
        failure_detail="盘前报告投递失败",
        artifact="a0_premarket",
    )


def _after_close(db: str, conn) -> JobResult:
    import invest.pipeline as pl
    collected = _collection_result(pl.collect(db), "after_close")
    if not collected.success:
        return collected
    pl.quant(db)
    # Agent 复盘/观点仲裁落库（不推送；晚间盘后报告统一在 22:00 推送，见 _evening_report）
    try:
        pl.agent_after_close(db)
        pl.arbitrate_all(db)
    except Exception as exc:
        logger.warning("盘后 Agent 复盘/仲裁失败: %s", exc)
    # 收盘扫描：因子快照 + 变化检测 + P1 推送（新入池/等级/评级变化）
    try:
        from invest.scan import run_scan_and_notify
        changes = run_scan_and_notify(db)
        if changes:
            _log_run(conn, "scan", "ok", f"P1 变化 {len(changes)} 条")
    except Exception as exc:
        logger.warning("收盘扫描失败: %s", exc)
    # 历史行业归属/ST 状态快照（[A]10）：每日收盘落库，供历史时点回溯
    try:
        from invest.data.universe import record_universe_snapshot
        n_uni = record_universe_snapshot(conn)
        if n_uni:
            _log_run(conn, "universe", "ok", f"快照 {n_uni} 个标的")
    except Exception as exc:
        logger.warning("历史快照失败: %s", exc)
    return collected


def _weekend(db: str, conn) -> JobResult:
    import invest.pipeline as pl
    from invest.review.report import save_report
    from invest.review.weekly import weekly_review
    pl.collect(db)
    pl.quant(db)
    review = weekly_review(conn)
    save_report(conn, review["period"], "weekly", review)
    # 周度纪律 + 周期漂移 + 持仓卡片复评（[A]6/[A]7）一并推送
    card_warns = [c for c in review["cards_review"] if c.get("hit_stop") or c.get("near_stop")]
    extra = ""
    if card_warns:
        extra = "\n持仓卡片警戒: " + "；".join(
            f"{c['symbol']}{'破止损' if c['hit_stop'] else '近止损'}" for c in card_warns
        )
    raw = pl.notify_weekend(
        db,
        f"纪律得分: {review['score']}；计划外交易 {review['rogue_trades']} 笔；"
        f"周期漂移 {review['cycle_drift']} 个计划" + extra,
        return_results=True,
    )
    return _delivery_result(
        raw,
        success_detail="周报投递成功",
        failure_detail="周报投递失败",
        artifact="a4_weekly",
    )


def _monthly(db: str, conn) -> JobResult:
    from invest.review.monthly import monthly_review
    from invest.review.report import save_report
    from invest.skills.runner import run as run_skill
    content = monthly_review(conn)
    save_report(conn, "monthly", "monthly", content)
    # 2026-08-22：摘要文案迁入 a5_monthly skill（content 传入避免重复计算）
    msg = run_skill("a5_monthly", db_path=db, content=content)
    raw = Notifier().send_text(
        msg,
        key="monthly",
        return_results=True,
        message_kind="report",
        message_id="a5_monthly",
    )
    return _delivery_result(
        raw,
        success_detail="月报投递成功",
        failure_detail="月报投递失败",
        artifact="a5_monthly",
    )


def _yearly(db: str, conn) -> JobResult:
    from invest.review.report import save_report
    from invest.review.yearly import yearly_review
    from invest.skills.runner import run as run_skill
    content = yearly_review(conn)
    save_report(conn, "yearly", "yearly", content)
    # 2026-08-22：摘要文案迁入 a6_yearly skill（content 传入避免重复计算）
    msg = run_skill("a6_yearly", db_path=db, content=content)
    raw = Notifier().send_text(
        msg,
        key="yearly",
        return_results=True,
        message_kind="report",
        message_id="a6_yearly",
    )
    return _delivery_result(
        raw,
        success_detail="年报投递成功",
        failure_detail="年报投递失败",
        artifact="a6_yearly",
    )


_last_pool_fetch = 0.0
_POOL_FETCH_INTERVAL = 300.0  # 涨停池/板块资金盘中每 5 分钟拉一次落库（10s ticker 内节流）


def _tick_collect_pools(db: str) -> None:
    """盘中顺带拉取涨停池个股明细 + 行业板块主力资金落库（2026-08-20）。

    每 5 分钟一次（由 _last_pool_fetch 节流）；失败只留痕不抛错（ticker 继续）。
    """
    global _last_pool_fetch
    now = time.time()
    if now - _last_pool_fetch < _POOL_FETCH_INTERVAL:
        return
    _last_pool_fetch = now
    conn = None
    try:
        from invest.data.emotion import fetch_limit_up_pool, today_str
        from invest.data.fund_flow import fetch_sector_fund_flow
        from invest.data.storage import upsert_df

        conn = connect(db)
        lup = fetch_limit_up_pool(today_str())
        n1 = upsert_df(conn, "limit_up_pool", lup) if not lup.empty else 0
        sff = fetch_sector_fund_flow()
        n2 = upsert_df(conn, "sector_fund_flow", sff) if not sff.empty else 0
        if n1 or n2:
            _log_run(conn, "pool_snapshot", "ok", f"涨停池 {n1} | 板块资金 {n2}")
    except Exception as exc:
        logger.warning("涨停池/板块资金采集失败: %s", exc)
    finally:
        if conn is not None:
            conn.close()


def _in_auction_window(now=None) -> bool:
    """竞价窗口：交易日 9:25:30-9:29:30（集合竞价结束后、开盘前）。"""
    from invest.data.calendar import is_trading_day

    now = now or dt.datetime.now()
    if not is_trading_day(now.date()):
        return False
    t = now.time()
    return dt.time(9, 25, 30) <= t <= dt.time(9, 29, 30)


def _auction_report(db: str, conn=None) -> JobResult:
    """生成并投递竞价报告；幂等和重试由持久化执行账本负责。"""
    from invest.pipeline import notify_auction

    raw = notify_auction(db, return_results=True)
    if isinstance(raw, JobResult):
        if not raw.artifact:
            return JobResult(raw.status, raw.detail, "a7_auction", raw.channel_results)
        return raw
    return _delivery_result(
        raw,
        success_detail="竞价报告投递成功",
        failure_detail="竞价报告投递失败",
        artifact="a7_auction",
    )


def _intraday_tick_job(
    db_path: str | None = None,
    now: dt.datetime | None = None,
) -> JobResult:
    """盘中 10 秒轮询 job 入口（无参，适配 APScheduler；2026-08-18 由 4s 降频到 10s）。

    - 竞价窗口（9:25:30-9:29:30）：触发竞价报告（当天一次），不跑盘中监控（未开盘）；
    - 非交易时段直接返回（守护，空转开销极小；行情旧属正常，不跑 P0 监控）；
    - 正常轮询不写 job_runs（避免每 10 秒一条噪音；实时健康留痕由
      log_realtime_health 节流承担，异常/stale 立即落库）；
    - 仅异动推送与失败留痕。
    """
    import invest.intraday as intr
    db = db_path or str(ROOT / "data" / "invest.db")
    now = now or dt.datetime.now()
    # 2026-08-22：竞价报告窗口（ticker-only 部署下也触发）
    if _in_auction_window(now):
        try:
            return _execute_job("auction", _auction_report, db, now=now)
        except JobExecutionError as exc:
            logger.warning("竞价报告异常: %s", exc)
            return JobResult.failed(str(exc))
    from invest.data.calendar import is_trading_day

    if is_trading_day(now.date()) and now.time() > dt.time(9, 29, 30):
        # 服务在竞价窗口后恢复时只登记 missed 并告警，绝不拿盘中数据补发。
        auction_state = _execute_job("auction", _auction_report, db, now=now)
        if auction_state.status == "missed":
            return auction_state
    if not intr._in_trading_window():
        return JobResult("skipped", "非盘中窗口")
    # 盘中涨停池/板块资金采集（5 分钟节流，2026-08-20）
    try:
        _tick_collect_pools(db)
    except Exception as exc:
        logger.warning("盘中池/资金采集异常: %s", exc)
    # P0 监控（仅交易时段：休市行情旧属正常，非交易时段不检查数据冲突）
    try:
        from invest.monitor import run_p0_monitor
        run_p0_monitor(db)
    except Exception as exc:
        logger.warning("P0 监控失败: %s", exc)
    conn = connect(db)
    try:
        alerts = intr.check_core_moves(db)
    except Exception as exc:
        try:
            _log_run(conn, "intraday", "failed", str(exc))
        except Exception:
            pass
        logger.warning("盘中轮询失败: %s", exc)
        return JobResult.failed(str(exc))
    finally:
        conn.close()
    if alerts:
        try:
            sent = intr.send_alerts(db, alerts)
        except Exception as exc:
            sent = 0
            logger.warning("盘中异动推送失败: %s", exc)
        conn2 = connect(db)
        try:
            detail = f"异动 {len(alerts)} 条，推送 {sent} 条"
            if not sent:
                _log_run(conn2, "intraday", "failed", detail)
                return JobResult.failed(detail, channel_results={"delivery": "failed"})
            _log_run(conn2, "intraday", "ok", detail)
        finally:
            conn2.close()
        return JobResult.ok(detail, channel_results={"delivery": "succeeded"})
    return JobResult.ok("alerts=0")



def _industry_refresh(db: str, conn) -> JobResult:
    """21:30 行业数据刷新：同花顺当天板块数据晚间才发布，
    刷新后 22:00 每日复盘即为当天板块涨幅/强度。"""
    import invest.pipeline as pl
    collected = _collection_result(
        pl.collect_industry(db),
        "industry_refresh",
        required={"industry_all"},
    )
    if not collected.success:
        return collected
    pl.quant(db)
    return collected


def _daily_refresh(db: str, conn) -> JobResult:
    """21:40 日线/指数补采（2026-08-17 修复数据滞后）。

    新浪/东财当日日线与指数日线晚间才发布，16:00 收盘采集拿不到
    当天数据（daily_bars/index_bars 滞后 1 个交易日）。此时补采
    并用当天数据重算 quant，保证 22:00 每日复盘数据是当天的。
    """
    import invest.pipeline as pl
    collected = _collection_result(
        pl.collect_bars_and_indices(db),
        "daily_refresh",
        required={"daily_bars", "index_bars"},
    )
    if not collected.success:
        return collected
    pl.quant(db)
    return collected


def _data_lag_reason(conn) -> str:
    """盘后数据新鲜度检查：daily_bars/index_bars 是否已更新到最近交易日。

    返回 "" = 数据新鲜可发报告；非空 = 滞后原因（此时不发报告，改为推送该原因）。
    2026-08-18 新增：当日日线/指数数据源晚间才发布（21:40 daily_refresh 补采），
    若补采未跑/失败，盘后报告拿到的就是上一交易日数据——必须先判断再发。
    """
    import datetime as dt

    from invest.data.calendar import latest_trading_day

    expected = latest_trading_day(dt.date.today()).isoformat()
    latest_bars = conn.execute("SELECT MAX(date) FROM daily_bars").fetchone()[0] or ""
    latest_idx = conn.execute("SELECT MAX(date) FROM index_bars").fetchone()[0] or ""
    if latest_bars >= expected and latest_idx >= expected:
        return ""
    ran = conn.execute(
        "SELECT COUNT(*) AS n FROM job_runs WHERE job='daily_refresh' "
        "AND date(started_at)=date('now','localtime')"
    ).fetchone()["n"]
    parts = [f"日线最新={latest_bars or '无'}，指数最新={latest_idx or '无'}，最近交易日={expected}"]
    parts.append("今日 21:40 日线补采任务未执行" if not ran else "今日 21:40 日线补采已执行但未取到当日数据")
    parts.append("当日日线/指数数据源通常晚间才发布；补采未跑或失败时只能拿到上一交易日数据")
    return "；".join(parts)


def _snapshot_close(db: str, conn) -> JobResult:
    """16:10 收盘快照落库（2026-08-20）：实时源直接写当日收盘价，不必等晚间日线发布。"""
    import invest.pipeline as pl
    counts = pl.snapshot_close(db)
    if not isinstance(counts, dict):
        return JobResult.failed("收盘快照未返回计数", artifact="snapshot_close")
    market = int(counts.get("market") or 0)
    index = int(counts.get("index") or 0)
    stock = int(counts.get("stock") or 0)
    detail = f"stock={stock} market={market} index={index}"
    if market <= 0 or index <= 0:
        return JobResult.failed(
            f"收盘快照关键产物缺失: {detail}",
            artifact="snapshot_close",
        )
    return JobResult.ok(detail, artifact="snapshot_close")


def _evening_report(db: str, conn) -> JobResult:
    """22:00 晚间盘后报告（合并原 16:00 盘后日报 / 21:35 P2 简报 / 22:00 每日复盘，只发一份）。

    数据新鲜度门禁（2026-08-18）：
    - 日线/指数已更新到最近交易日 → 正常生成并推送合并报告（daily_report + 复盘统计 + 数据质量）；
    - 滞后 → 不发送报告，改为推送一条滞后原因（12h 限频），并把原因写入 job_runs 留痕。
    """
    reason = _data_lag_reason(conn)
    if reason:
        warned = Notifier().send_text(
            f"⚠️【盘后报告未发送】数据滞后：{reason}",
            key="evening_stale",
            min_interval=43200,
            message_kind="alert",
            message_id="evening_stale",
        )
        return JobResult(
            "skipped",
            reason,
            channel_results={"stale_warning": warned},
        )

    # 到期观点/工单（原 nightly 内容）
    from invest.agent.tickets import expire_overdue
    from invest.viewpoints.store import expire_due

    expired = expire_due(conn)
    overdue = expire_overdue(conn)
    new_vp = conn.execute(
        "SELECT COUNT(*) FROM viewpoints WHERE date(created_at)=date('now','localtime')"
    ).fetchone()[0]

    # 2026-08-22：盘后日报经 Skill Runner 调 a3_daily（结构化，4 点 + 预案闭环）
    from invest.skills.runner import run_structured

    struct = run_structured("a3_daily", db_path=db)
    tail = f"【今日】到期进复盘 {expired} 条 | 工单超时 {overdue} 张 | 新增观点 {new_vp} 条"
    # 数据质量报告（PIT 四状态）追加
    try:
        from invest.data.pit import quality_report

        report = quality_report(conn)
        bad = {t: st for t, (st, _info) in report.items() if st != "valid"}
        if bad:
            tail += "\n数据质量: " + ", ".join(f"{t}={st}" for t, st in list(bad.items())[:8])
    except Exception as exc:
        logger.warning("数据质量报告失败: %s", exc)
    struct["sections"].append({"type": "text", "text": tail})
    # 明日预案落库（source='plan'，供 B1 对照 / 次日预案质量复盘）
    try:
        from invest.pipeline import _persist_plan

        _persist_plan(struct.get("plan_data") or {})
    except Exception as exc:
        logger.warning("预案落库失败: %s", exc)
    # 发送：飞书卡片 + 企微/微信纯文本
    from invest.pipeline import _send_structured

    raw_result = _send_structured(
        struct,
        key="evening_report",
        min_interval=600,
        return_results=True,
        message_kind="report",
        message_id="a3_daily",
    )
    channels = (
        raw_result
        if isinstance(raw_result, dict)
        else {"delivery": bool(raw_result)}
    )
    if not any(channels.values()):
        time.sleep(5)  # 网络抖动时重试一次
        raw_result = _send_structured(
            struct,
            key="evening_report",
            min_interval=600,
            return_results=True,
            message_kind="report",
            message_id="a3_daily",
        )
        channels = (
            raw_result
            if isinstance(raw_result, dict)
            else {"delivery": bool(raw_result)}
        )
    if not any(channels.values()):
        logger.warning("22:00 盘后报告推送失败或未配置 webhook")
        return JobResult.failed(
            "盘后报告推送失败或未配置 webhook",
            artifact="a3_daily",
            channel_results=channels,
        )
    return JobResult.ok(
        "盘后报告投递成功",
        artifact="a3_daily",
        channel_results=channels,
    )


def _pool_trap_scan(db: str, conn) -> JobResult:
    """17:10 候选池/持仓杀猪盘 8 信号扫描（2026-08-23，d31_pool_trap_alerts 复用）。

    写 pool_trap_alerts 表（全部结果留痕）+ 有 ≥🟡 预警推送飞书（1h 限频）。
    """
    import datetime as _dt
    import json

    from invest.data.storage import upsert_df
    from invest.skills.sections.d31_pool_trap_alerts import scan_pool

    alerts = scan_pool(conn)
    if alerts:
        import pandas as _pd

        upsert_df(conn, "pool_trap_alerts", _pd.DataFrame([{
            "date": _dt.date.today().isoformat(),
            "symbol": a["symbol"], "level": a["level"], "trap_score": a["trap_score"],
            "signals_hit": json.dumps(a["signals_hit"], ensure_ascii=False),
            "recommendation": a["recommendation"],
        } for a in alerts]))
    warn = [a for a in alerts if a["level"] in ("🟡", "🟠", "🔴")]
    if warn:
        lines = ["⚠️【候选池预警 · 杀猪盘扫描】"]
        for a in warn:
            lines.append(f"  {a['symbol']} {a['name'] or ''} {a['level']} 命中{len(a['signals_hit'])}信号")
            for s in a["signals_hit"]:
                lines.append(f"    · {s['name']}: {(s.get('evidence') or '')[:60]}")
        raw = Notifier().send_text(
            "\n".join(lines),
            key="pool_trap",
            min_interval=3600,
            return_results=True,
            message_kind="alert",
            message_id="d31_pool_trap_alerts",
        )
        return _delivery_result(
            raw,
            success_detail="候选池预警投递成功",
            failure_detail="候选池预警投递失败",
            artifact="d31_pool_trap_alerts",
        )
    return JobResult.ok("无候选池预警", artifact="d31_pool_trap_alerts")


def _factcard_refresh(db: str, conn) -> JobResult:
    """21:50 行业事实卡重建；仅推送相对上一时点发生重要变化的摘要+证据编号。"""
    from invest.evidence.factcards import run_factcard_refresh

    return run_factcard_refresh(db, conn, push=True)


# 单任务执行入口（供操作系统计划任务调用，见 scripts/run_job.py 与 install_os_tasks.ps1）
JOB_FUNCS: dict[str, Callable] = {
    "premarket": _premarket,
    "morning_brief": _morning_brief,
    "auction": _auction_report,  # 2026-08-22：竞价报告（OS 任务重装后 9:26 触发）
    "after_close": _after_close,
    "snapshot_close": _snapshot_close,
    "weekend": _weekend,
    "monthly": _monthly,
    "yearly": _yearly,
    "industry_refresh": _industry_refresh,
    "daily_refresh": _daily_refresh,
    "factcard_refresh": _factcard_refresh,
    "evening_report": _evening_report,
    "pool_trap_scan": _pool_trap_scan,  # 2026-08-23：候选池杀猪盘扫描
}


def _job_is_scheduled_today(job_name: str, date: dt.date) -> bool:
    """判断补偿扫描当天是否应有该计划槽位。"""
    if job_name in TRADING_DAY_JOBS or job_name == "evening_report":
        from invest.data.calendar import is_trading_day

        return bool(is_trading_day(date))
    if job_name == "weekend":
        return date.weekday() == 6
    if job_name == "monthly":
        return date.day == 1
    if job_name == "yearly":
        return date.month == 1 and date.day == 1
    return False


def _record_compensation_missed(
    job_name: str,
    db: str,
    now: dt.datetime,
    window_end: dt.time,
) -> JobResult:
    """窗口外原子记录 missed 并逐通道告警，不执行任务正文。"""
    from invest.delivery import delivery_context

    scheduled_date = now.date().isoformat()
    run_slot = JOB_SLOTS[job_name]
    _ensure_db_initialized(db)
    conn = connect(db)
    try:
        claimed, existing, lease_owner = _claim_execution(
            conn,
            job_name,
            scheduled_date,
            run_slot,
            DEFAULT_JOB_LEASE_SECONDS,
        )
        if not claimed:
            if existing == "ok":
                return JobResult("already_ok", "该计划槽位已成功")
            if existing == "missed":
                return JobResult("already_missed", "漏跑已记录")
            return JobResult("already_running", "该计划槽位正在执行")

        detail = (
            f"任务 {job_name} 已超过补偿窗口"
            f"（截止 {window_end.isoformat()}），不再补发"
        )
        message_id = f"{job_name}_missed"
        try:
            with delivery_context(
                db,
                job_name,
                scheduled_date,
                run_slot,
            ) as warning_delivery:
                raw = Notifier().send_text(
                    f"⚠️ {detail}",
                    key=f"{message_id}_{scheduled_date}",
                    return_results=True,
                    message_kind="alert",
                    message_id=message_id,
                )
            channels = dict(warning_delivery.channel_states)
            if not channels:
                raw_channels = raw if isinstance(raw, dict) else {"warning": bool(raw)}
                channels = {
                    f"alert/{message_id}/{channel}": (
                        "succeeded" if ok else "failed"
                    )
                    for channel, ok in raw_channels.items()
                }
        except Exception:
            logger.warning("%s 漏跑告警推送异常", job_name, exc_info=True)
            channels = {f"alert/{message_id}/warning": "uncertain"}
        result = JobResult("missed", detail, channel_results=channels)
        _finish_execution(
            conn,
            job_name,
            scheduled_date,
            run_slot,
            result,
            lease_owner,
        )
        return result
    finally:
        conn.close()


def run_compensation_scan(
    *,
    db_path: str | None = None,
    now: dt.datetime | None = None,
    jobs: set[str] | None = None,
) -> dict[str, JobResult]:
    """扫描当天应运行槽位：窗口内补跑，窗口外记 missed。"""
    db = db_path or str(ROOT / "data" / "invest.db")
    run_now = now or dt.datetime.now()
    selected = jobs or set(JOB_COMPENSATION_WINDOWS)
    results: dict[str, JobResult] = {}
    candidates = (
        name
        for name in selected
        if name in JOB_FUNCS and name in JOB_COMPENSATION_WINDOWS
    )
    for job_name in sorted(
        candidates,
        key=lambda name: JOB_COMPENSATION_WINDOWS[name][0],
    ):
        if not _job_is_scheduled_today(job_name, run_now.date()):
            continue
        window_start, window_end = JOB_COMPENSATION_WINDOWS[job_name]
        if run_now.time() < window_start:
            continue
        try:
            if run_now.time() <= window_end:
                results[job_name] = _execute_job(
                    job_name,
                    JOB_FUNCS[job_name],
                    db,
                    now=run_now,
                )
            elif job_name == "auction":
                # 复用竞价专用过窗保护：只告警并记 missed，绝不生成竞价报告。
                results[job_name] = _execute_job(
                    job_name,
                    JOB_FUNCS[job_name],
                    db,
                    now=run_now,
                )
            else:
                results[job_name] = _record_compensation_missed(
                    job_name,
                    db,
                    run_now,
                    window_end,
                )
        except JobExecutionError as exc:
            # 单任务失败不能中断其余槽位扫描；下次周期仍会基于 failed 状态重试。
            results[job_name] = JobResult.failed(str(exc))
        except Exception as exc:
            logger.exception("补偿扫描任务 %s 异常", job_name)
            results[job_name] = JobResult.failed(str(exc))
    return results


def run_job_once(
    job_name: str,
    *,
    db_path: str | None = None,
    now: dt.datetime | None = None,
    lease_seconds: int | None = None,
    heartbeat_interval: float | None = None,
    wait_for_running: float = 0.0,
) -> JobResult:
    """执行单个定时任务（含 running/ok/failed 留痕与失败推送，语义同 APScheduler _wrap）。

    job_name 不在 JOB_FUNCS 时抛 ValueError（调用方应捕获并给出错误码）。
    """
    fn = JOB_FUNCS.get(job_name)
    if fn is None:
        raise ValueError(f"未知任务: {job_name}，可选: {sorted(JOB_FUNCS)}")
    db = db_path or str(ROOT / "data" / "invest.db")
    run_now = now or dt.datetime.now()
    result = _execute_job(
        job_name,
        fn,
        db,
        now=run_now,
        lease_seconds=lease_seconds,
        heartbeat_interval=heartbeat_interval,
    )
    if result.status != "already_running" or wait_for_running <= 0:
        return result

    scheduled_date = run_now.date().isoformat()
    run_slot = JOB_SLOTS.get(job_name, "manual")
    deadline = time.monotonic() + wait_for_running
    while time.monotonic() < deadline:
        time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))
        conn = connect(db)
        try:
            row = conn.execute(
                """SELECT status, detail, artifact, channel_results
                   FROM job_executions
                   WHERE job=? AND scheduled_date=? AND run_slot=?""",
                (job_name, scheduled_date, run_slot),
            ).fetchone()
        finally:
            conn.close()
        if row and row["status"] != "running":
            channels = json.loads(row["channel_results"] or "{}")
            status = "already_ok" if row["status"] == "ok" else row["status"]
            return JobResult(
                status,
                row["detail"] or "",
                row["artifact"] or "",
                channels,
            )
    return JobResult(
        "deferred",
        f"同槽任务仍在执行，已等待 {wait_for_running:g} 秒",
    )


def build_scheduler(ticker_only: bool = False) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    # 服务启动后立即扫描一次，之后每分钟补偿；max_instances 防扫描自身重叠。
    sched.add_job(
        run_compensation_scan,
        IntervalTrigger(minutes=1),
        id="compensation_scan",
        next_run_time=dt.datetime.now(),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    if ticker_only:
        # OS 计划任务模式：保留盘中 10 秒轮询，并每分钟扫描 OS 任务漏跑/失败补偿。
        sched.add_job(
            _intraday_tick_job,
            IntervalTrigger(seconds=10),
            id="intraday_tick",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        return sched
    sched.add_job(_wrap("premarket", _premarket), CronTrigger(day_of_week="mon-fri", hour=8, minute=30), id="premarket", misfire_grace_time=21600)
    # 盘前信息早报（2026-08-16）：8:30 采集+quant 后，8:40 发关键信息早报
    sched.add_job(_wrap("morning_brief", _morning_brief), CronTrigger(day_of_week="mon-fri", hour=8, minute=40), id="morning_brief", misfire_grace_time=21600)
    # 竞价报告（2026-08-22）：9:26（ticker-only 部署由 _intraday_tick_job 竞价窗口触发，这里为 full 模式备选）
    sched.add_job(_wrap("auction", _auction_report), CronTrigger(day_of_week="mon-fri", hour=9, minute=26), id="auction", misfire_grace_time=300)
    sched.add_job(_wrap("after_close", _after_close), CronTrigger(day_of_week="mon-fri", hour=16, minute=0), id="after_close", misfire_grace_time=21600)
    # 收盘即日线（2026-08-20 初版 16:10；2026-08-24 提前到 15:01 并升级全市场 OHLCV）：
    # 东财 clist 批量接口 15:00 收盘后立即返回全市场当日 OHLCV，15:01 落库 src='snapshot'，
    # 不必等晚间 akshare 日线（约 21 点）；晚间权威数据写入后自动删当日 snapshot 行
    sched.add_job(_wrap("snapshot_close", _snapshot_close), CronTrigger(day_of_week="mon-fri", hour=15, minute=1), id="snapshot_close", misfire_grace_time=7200)
    # 候选池杀猪盘扫描（2026-08-23）：17:10 全 8 信号扫描候选池/持仓，≥🟡 推送
    sched.add_job(_wrap("pool_trap_scan", _pool_trap_scan), CronTrigger(day_of_week="mon-fri", hour=17, minute=10), id="pool_trap_scan", misfire_grace_time=7200)
    # 周末周报（2026-08-18 改）：周日 20:00（原周六 09:00）——晚间数据齐备后发，
    # 内容含消息面（财联社电报近7日）+ 周度复盘（纪律/周期漂移/持仓卡片复评）
    sched.add_job(_wrap("weekend", _weekend), CronTrigger(day_of_week="sun", hour=20, minute=0), id="weekend", misfire_grace_time=21600)
    sched.add_job(_wrap("monthly", _monthly), CronTrigger(day="1", hour=9, minute=30), id="monthly", misfire_grace_time=43200)
    sched.add_job(_wrap("yearly", _yearly), CronTrigger(month="1", day="1", hour=9, minute=30), id="yearly", misfire_grace_time=43200)
    # 盘中实时行情：10 秒高频轮询（2026-08-18 由 4s 降频；三源直连）
    sched.add_job(
        _intraday_tick_job,
        IntervalTrigger(seconds=10),
        id="intraday_tick",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    sched.add_job(_wrap("industry_refresh", _industry_refresh), CronTrigger(day_of_week="mon-fri", hour=21, minute=30), id="industry_refresh", misfire_grace_time=3600)
    # 日线/指数补采（2026-08-17）：当日日线晚间才发布，收盘采集拿不到当天数据
    sched.add_job(_wrap("daily_refresh", _daily_refresh), CronTrigger(day_of_week="mon-fri", hour=21, minute=40), id="daily_refresh", misfire_grace_time=3600)
    sched.add_job(_wrap("factcard_refresh", _factcard_refresh), CronTrigger(day_of_week="mon-fri", hour=21, minute=50), id="factcard_refresh", misfire_grace_time=3600)
    # 晚间盘后报告（2026-08-18 合并 daily_report/P2简报/每日复盘）：22:00 只发一份；
    # 数据滞后时跳过并推送原因（_data_lag_reason 门禁）
    sched.add_job(_wrap("evening_report", _evening_report), CronTrigger(hour=22, minute=0), id="evening_report", misfire_grace_time=7200)
    return sched
