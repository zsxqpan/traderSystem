"""调度器：盘前/盘后/周末/夜间例行任务（APScheduler）。

盘中实时行情通道（2026-08-18 决策）：独立 ticker 每 10 秒轮询核心池，
非交易时段由 _in_trading_window 守护（空转无副作用）；
异动推送与失败留痕照常，正常轮询不写 job_runs（留痕由
log_realtime_health 节流承担，正常 60s 一条基线、异常立即记）。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

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
            result = fn(db, conn)
            detail = str(result) if result else ""
            _log_run(conn, job_name, "ok", detail)
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
                except Exception as exc:  # noqa: BLE001
                    last_err = f"{type(exc).__name__}"
                    time.sleep(3)
            else:
                detail += f" | webhook=unreachable({last_err})"
        else:
            detail += " | webhook=not_configured"
    except Exception as exc:  # noqa: BLE001
        detail += f" | webhook=check_failed({type(exc).__name__})"
    try:
        init_db(db)
        conn = connect(db)
        try:
            _log_run(conn, "scheduler", "running", detail)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("服务启动日志写入失败")


def _premarket(db: str, conn) -> None:
    import invest.pipeline as pl
    pl.collect(db)
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("环境重评检查失败: %s", exc)
    text = pl.agent_premarket(db)
    pl.notify_premarket(db, text)


def _morning_brief(db: str, conn) -> None:
    """盘前信息早报（2026-08-16）：8:40，关键信息简明扼要（8:30 采集后发）。"""
    import invest.pipeline as pl
    pl.notify_morning_brief(db)


def _after_close(db: str, conn) -> None:
    import invest.pipeline as pl
    pl.collect(db)
    pl.quant(db)
    # Agent 复盘/观点仲裁落库（不推送；晚间盘后报告统一在 22:00 推送，见 _evening_report）
    try:
        pl.agent_after_close(db)
        pl.arbitrate_all(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("盘后 Agent 复盘/仲裁失败: %s", exc)
    # 收盘扫描：因子快照 + 变化检测 + P1 推送（新入池/等级/评级变化）
    try:
        from invest.scan import run_scan_and_notify
        changes = run_scan_and_notify(db)
        if changes:
            _log_run(conn, "scan", "ok", f"P1 变化 {len(changes)} 条")
    except Exception as exc:  # noqa: BLE001
        logger.warning("收盘扫描失败: %s", exc)
    # 历史行业归属/ST 状态快照（[A]10）：每日收盘落库，供历史时点回溯
    try:
        from invest.data.universe import record_universe_snapshot
        n_uni = record_universe_snapshot(conn)
        if n_uni:
            _log_run(conn, "universe", "ok", f"快照 {n_uni} 个标的")
    except Exception as exc:  # noqa: BLE001
        logger.warning("历史快照失败: %s", exc)


def _weekend(db: str, conn) -> None:
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
    pl.notify_weekend(
        db,
        f"纪律得分: {review['score']}；计划外交易 {review['rogue_trades']} 笔；"
        f"周期漂移 {review['cycle_drift']} 个计划" + extra,
    )


def _monthly(db: str, conn) -> None:
    from invest.review.monthly import monthly_review
    from invest.review.report import save_report
    content = monthly_review(conn)
    save_report(conn, "monthly", "monthly", content)
    env = content.get("environment_quality", {})
    env_note = ""
    if env.get("verdict") == "warn":
        env_note = "；环境质量告警: " + "；".join(env.get("warnings", []))
    Notifier().send_text(
        f"月度复盘: 观点命中率 {content['overall_accuracy'] if content['overall_accuracy'] is not None else '暂无'} | "
        f"待复盘 {content['pending_review']} 条 | 环境质量 {env.get('verdict', 'ok')}{env_note}",
        key="monthly",
    )


def _yearly(db: str, conn) -> None:
    from invest.review.report import save_report
    from invest.review.yearly import yearly_review
    content = yearly_review(conn)
    save_report(conn, "yearly", "yearly", content)
    Notifier().send_text(f"年度复盘已生成: {len(content['backtest_summary'])} 组回测结论待检视", key="yearly")


def _intraday_tick_job() -> None:
    """盘中 10 秒轮询 job 入口（无参，适配 APScheduler；2026-08-18 由 4s 降频到 10s）。

    - 非交易时段直接返回（守护，空转开销极小；行情旧属正常，不跑 P0 监控）；
    - 正常轮询不写 job_runs（避免每 10 秒一条噪音；实时健康留痕由
      log_realtime_health 节流承担，异常/stale 立即落库）；
    - 仅异动推送与失败留痕。
    """
    import invest.intraday as intr
    db = str(ROOT / "data" / "invest.db")
    if not intr._in_trading_window():
        return
    # P0 监控（仅交易时段：休市行情旧属正常，非交易时段不检查数据冲突）
    try:
        from invest.monitor import run_p0_monitor
        run_p0_monitor(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("P0 监控失败: %s", exc)
    conn = connect(db)
    try:
        alerts = intr.check_core_moves(db)
    except Exception as exc:  # noqa: BLE001
        try:
            _log_run(conn, "intraday", "failed", str(exc))
        except Exception:  # noqa: BLE001
            pass
        logger.warning("盘中轮询失败: %s", exc)
        return
    finally:
        conn.close()
    if alerts:
        sent = intr.send_alerts(db, alerts)
        conn2 = connect(db)
        try:
            _log_run(conn2, "intraday", "ok", f"异动 {len(alerts)} 条，推送 {sent} 条")
        finally:
            conn2.close()



def _industry_refresh(db: str, conn) -> None:
    """21:30 行业数据刷新：同花顺当天板块数据晚间才发布，
    刷新后 22:00 每日复盘即为当天板块涨幅/强度。"""
    import invest.pipeline as pl
    pl.collect_industry(db)
    pl.quant(db)


def _daily_refresh(db: str, conn) -> None:
    """21:40 日线/指数补采（2026-08-17 修复数据滞后）。

    新浪/东财当日日线与指数日线晚间才发布，16:00 收盘采集拿不到
    当天数据（daily_bars/index_bars 滞后 1 个交易日）。此时补采
    并用当天数据重算 quant，保证 22:00 每日复盘数据是当天的。
    """
    import invest.pipeline as pl
    pl.collect_bars_and_indices(db)
    pl.quant(db)


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


def _evening_report(db: str, conn) -> None:
    """22:00 晚间盘后报告（合并原 16:00 盘后日报 / 21:35 P2 简报 / 22:00 每日复盘，只发一份）。

    数据新鲜度门禁（2026-08-18）：
    - 日线/指数已更新到最近交易日 → 正常生成并推送合并报告（daily_report + 复盘统计 + 数据质量）；
    - 滞后 → 不发送报告，改为推送一条滞后原因（12h 限频），并把原因写入 job_runs 留痕。
    """
    reason = _data_lag_reason(conn)
    if reason:
        _log_run(conn, "evening_report", "skipped", reason)
        Notifier().send_text(f"⚠️【盘后报告未发送】数据滞后：{reason}", key="evening_stale", min_interval=43200)
        return "stale_skip"

    # 到期观点/工单（原 nightly 内容）
    from invest.agent.tickets import expire_overdue
    from invest.viewpoints.store import expire_due

    expired = expire_due(conn)
    overdue = expire_overdue(conn)
    new_vp = conn.execute(
        "SELECT COUNT(*) FROM viewpoints WHERE date(created_at)=date('now','localtime')"
    ).fetchone()[0]

    from invest.report import daily_report

    msg = daily_report(db)
    msg += f"\n【今日】到期进复盘 {expired} 条 | 工单超时 {overdue} 张 | 新增观点 {new_vp} 条"
    # 数据质量报告（PIT 四状态）追加
    try:
        from invest.data.pit import quality_report

        report = quality_report(conn)
        bad = {t: st for t, (st, _info) in report.items() if st != "valid"}
        if bad:
            msg += "\n数据质量: " + ", ".join(f"{t}={st}" for t, st in list(bad.items())[:8])
    except Exception as exc:  # noqa: BLE001
        logger.warning("数据质量报告失败: %s", exc)
    ok = Notifier().send_text(msg, key="evening_report", min_interval=600)
    if not ok:
        time.sleep(5)  # 网络抖动时重试一次
        ok = Notifier().send_text(msg, key="evening_report", min_interval=600)
    if not ok:
        logger.warning("22:00 盘后报告推送失败或未配置 webhook")
        return "push_failed(webhook unreachable)"
    return "push_ok"


# 单任务执行入口（供操作系统计划任务调用，见 scripts/run_job.py 与 install_os_tasks.ps1）
JOB_FUNCS: dict[str, callable] = {
    "premarket": _premarket,
    "morning_brief": _morning_brief,
    "after_close": _after_close,
    "weekend": _weekend,
    "monthly": _monthly,
    "yearly": _yearly,
    "industry_refresh": _industry_refresh,
    "daily_refresh": _daily_refresh,
    "evening_report": _evening_report,
}


def run_job_once(job_name: str) -> None:
    """执行单个定时任务（含 running/ok/failed 留痕与失败推送，语义同 APScheduler _wrap）。

    job_name 不在 JOB_FUNCS 时抛 ValueError（调用方应捕获并给出错误码）。
    """
    fn = JOB_FUNCS.get(job_name)
    if fn is None:
        raise ValueError(f"未知任务: {job_name}，可选: {sorted(JOB_FUNCS)}")
    _wrap(job_name, fn)()


def build_scheduler(ticker_only: bool = False) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    if ticker_only:
        # 2026-08-18：OS 计划任务模式——只保留盘中 10 秒轮询（OS 任务无法低于 1 分钟粒度）
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
    sched.add_job(_wrap("after_close", _after_close), CronTrigger(day_of_week="mon-fri", hour=16, minute=0), id="after_close", misfire_grace_time=21600)
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
    # 晚间盘后报告（2026-08-18 合并 daily_report/P2简报/每日复盘）：22:00 只发一份；
    # 数据滞后时跳过并推送原因（_data_lag_reason 门禁）
    sched.add_job(_wrap("evening_report", _evening_report), CronTrigger(hour=22, minute=0), id="evening_report", misfire_grace_time=7200)
    return sched
