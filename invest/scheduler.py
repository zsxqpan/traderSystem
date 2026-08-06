"""调度器：盘前/盘后/周末/夜间例行任务（APScheduler）。"""
from __future__ import annotations

import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from invest.db import connect, init_db
from invest.notifier import Notifier

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def _log_run(conn, job: str, status: str, detail: str = "") -> None:
    with conn:
        conn.execute(
            """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
               VALUES(?, ?, datetime('now','localtime'), datetime('now','localtime'), ?)""",
            (job, status, detail),
        )


def _wrap(job_name: str, fn) -> callable:
    def run() -> None:
        db = str(ROOT / "data" / "invest.db")
        conn = None
        try:
            init_db(db)
            conn = connect(db)
            _log_run(conn, job_name, "running")  # 开始即记录，卡死也可见
            fn(db, conn)
            _log_run(conn, job_name, "ok")
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s 失败", job_name)
            if conn is not None:
                _log_run(conn, job_name, "failed", str(exc))
            try:
                Notifier().send_text(f"任务 {job_name} 失败: {exc}", key=job_name)
            except Exception:  # noqa: BLE001
                logger.warning("失败告警推送异常: %s", exc)
        finally:
            if conn is not None:
                conn.close()
    return run


def log_service_started() -> None:
    """服务启动时写一条 job_runs，便于确认调度器确实活着。"""
    db = str(ROOT / "data" / "invest.db")
    try:
        init_db(db)
        conn = connect(db)
        try:
            _log_run(conn, "scheduler", "running", "service started")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("服务启动日志写入失败")


def _premarket(db: str, conn) -> None:
    import invest.pipeline as pl
    pl.collect(db)
    pl.quant(db)
    text = pl.agent_premarket(db)
    pl.notify_premarket(db, text)


def _after_close(db: str, conn) -> None:
    import invest.pipeline as pl
    pl.collect(db)
    pl.quant(db)
    text = pl.agent_after_close(db)
    n_conflict = pl.arbitrate_all(db)
    pl.notify_after_close(db, text if n_conflict == 0 else f"{text}\n[自动仲裁 {n_conflict} 对冲突]")


def _weekend(db: str, conn) -> None:
    import invest.pipeline as pl
    from invest.review.report import save_report
    from invest.review.weekly import weekly_review
    pl.collect(db)
    pl.quant(db)
    review = weekly_review(conn)
    save_report(conn, review["period"], "weekly", review)
    pl.notify_weekend(db, f"纪律得分: {review['score']}；计划外交易 {review['rogue_trades']} 笔")


def _monthly(db: str, conn) -> None:
    from invest.review.monthly import monthly_review
    from invest.review.report import save_report
    content = monthly_review(conn)
    save_report(conn, "monthly", "monthly", content)
    Notifier().send_text(
        f"月度复盘: 观点命中率 {content['overall_accuracy'] if content['overall_accuracy'] is not None else '暂无'} | 待复盘 {content['pending_review']} 条",
        key="monthly",
    )


def _yearly(db: str, conn) -> None:
    from invest.review.report import save_report
    from invest.review.yearly import yearly_review
    content = yearly_review(conn)
    save_report(conn, "yearly", "yearly", content)
    Notifier().send_text(f"年度复盘已生成: {len(content['backtest_summary'])} 组回测结论待检视", key="yearly")


def _intraday(db: str, conn) -> None:
    import invest.intraday as intr
    if not intr._in_trading_window():
        return
    try:
        alerts = intr.check_core_moves(db)
    except Exception as exc:  # noqa: BLE001
        _log_run(conn, "intraday", "failed", str(exc))
        return
    if alerts:
        sent = intr.send_alerts(db, alerts)
        _log_run(conn, "intraday", "ok", f"异动 {len(alerts)} 条，推送 {sent} 条")
    else:
        _log_run(conn, "intraday", "ok", "无异动")


def _nightly(db: str, conn) -> None:
    """22:00 每日复盘：到期观点/工单入队 + 固定推送一份当日状态（无事也发）。"""
    from invest.agent.tickets import expire_overdue
    from invest.viewpoints.store import expire_due
    from invest import pipeline as pl
    expired = expire_due(conn)
    overdue = expire_overdue(conn)
    new_vp = conn.execute(
        "SELECT COUNT(*) FROM viewpoints WHERE date(created_at)=date('now','localtime')"
    ).fetchone()[0]
    msg = (
        f"【A股投资系统 · 每日复盘】\n"
        f"数据截至: {pl._latest_data_date(conn)}\n"
        f"市场温度: {pl._temperature(conn)}\n"
        f"短线强度前5: {pl._top_strength(conn, 'short')}\n"
        f"中线强度前3: {pl._top_strength(conn, 'mid', 3)}\n"
        f"今日新增观点: {new_vp} 条 | 到期进复盘: {expired} 条 | 工单超时: {overdue} 张"
    )
    ok = Notifier().send_text(msg, key="nightly")
    if not ok:
        logger.warning("22:00 复盘推送失败或未配置 webhook")


def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(_wrap("premarket", _premarket), CronTrigger(day_of_week="mon-fri", hour=8, minute=30), id="premarket")
    sched.add_job(_wrap("after_close", _after_close), CronTrigger(day_of_week="mon-fri", hour=16, minute=0), id="after_close")
    sched.add_job(_wrap("weekend", _weekend), CronTrigger(day_of_week="sat", hour=9, minute=0), id="weekend")
    sched.add_job(_wrap("monthly", _monthly), CronTrigger(day="1", hour=9, minute=30), id="monthly")
    sched.add_job(_wrap("yearly", _yearly), CronTrigger(month="1", day="1", hour=9, minute=30), id="yearly")
    sched.add_job(_wrap("intraday", _intraday), CronTrigger(day_of_week="mon-fri", hour="9-11,13-14", minute="*/5"), id="intraday")
    sched.add_job(_wrap("nightly", _nightly), CronTrigger(hour=22, minute=0), id="nightly")
    return sched