"""调度器：盘前/盘后/周末/夜间例行任务（APScheduler）。

盘中实时行情通道（2026-08-18 决策）：独立 ticker 每 10 秒轮询核心池，
非交易时段由 _in_trading_window 守护（空转无副作用）；
异动推送与失败留痕照常，正常轮询不写 job_runs（留痕由
log_realtime_health 节流承担，正常 60s 一条基线、异常立即记）。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
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


def _wrap(job_name: str, fn) -> Callable:
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
        except Exception as exc:
            logger.exception("%s 失败", job_name)
            if conn is not None:
                _log_run(conn, job_name, "failed", str(exc))
            try:
                Notifier().send_text(f"任务 {job_name} 失败: {exc}", key=job_name)
            except Exception:
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
        init_db(db)
        conn = connect(db)
        try:
            _log_run(conn, "scheduler", "running", detail)
        finally:
            conn.close()
    except Exception:
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
    except Exception as exc:
        logger.warning("环境重评检查失败: %s", exc)
    text = pl.agent_premarket(db)
    # 2026-08-22：不再直接推送 A1；Agent 关注方向落盘，供 8:40 盘前报告(a0)「今日关注」节
    try:
        (ROOT / "data" / "premarket_agent.txt").write_text(text or "", encoding="utf-8")
    except Exception as exc:
        logger.warning("盘前 Agent 关注方向落盘失败: %s", exc)


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
    from invest.skills.runner import run as run_skill
    content = monthly_review(conn)
    save_report(conn, "monthly", "monthly", content)
    # 2026-08-22：摘要文案迁入 a5_monthly skill（content 传入避免重复计算）
    msg = run_skill("a5_monthly", db_path=db, content=content)
    Notifier().send_text(msg, key="monthly")


def _yearly(db: str, conn) -> None:
    from invest.review.report import save_report
    from invest.review.yearly import yearly_review
    from invest.skills.runner import run as run_skill
    content = yearly_review(conn)
    save_report(conn, "yearly", "yearly", content)
    # 2026-08-22：摘要文案迁入 a6_yearly skill（content 传入避免重复计算）
    msg = run_skill("a6_yearly", db_path=db, content=content)
    Notifier().send_text(msg, key="yearly")


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
    import datetime as dt

    now = now or dt.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt.time(9, 25, 30) <= t <= dt.time(9, 29, 30)


_auction_sent: str = ""  # 当天已发竞价报告的日期 YYYY-MM-DD（防重）


def _auction_report(db: str, conn=None) -> bool:
    """竞价报告（2026-08-22）：9:25 集合竞价结束后推送，当天一次。"""
    global _auction_sent
    import datetime as dt

    today = dt.date.today().isoformat()
    if _auction_sent == today:
        return False
    try:
        from invest.pipeline import notify_auction

        ok = notify_auction(db)
        _auction_sent = today
        if conn is not None:
            _log_run(conn, "auction", "ok", f"push={ok}")
        return ok
    except Exception:
        logger.exception("竞价报告失败")
        return False


def _intraday_tick_job() -> None:
    """盘中 10 秒轮询 job 入口（无参，适配 APScheduler；2026-08-18 由 4s 降频到 10s）。

    - 竞价窗口（9:25:30-9:29:30）：触发竞价报告（当天一次），不跑盘中监控（未开盘）；
    - 非交易时段直接返回（守护，空转开销极小；行情旧属正常，不跑 P0 监控）；
    - 正常轮询不写 job_runs（避免每 10 秒一条噪音；实时健康留痕由
      log_realtime_health 节流承担，异常/stale 立即落库）；
    - 仅异动推送与失败留痕。
    """
    import invest.intraday as intr
    db = str(ROOT / "data" / "invest.db")
    # 2026-08-22：竞价报告窗口（ticker-only 部署下也触发）
    if _in_auction_window():
        try:
            _auction_report(db)
        except Exception as exc:
            logger.warning("竞价报告异常: %s", exc)
        return
    if not intr._in_trading_window():
        return
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


def _snapshot_close(db: str, conn) -> None:
    """16:10 收盘快照落库（2026-08-20）：实时源直接写当日收盘价，不必等晚间日线发布。"""
    import invest.pipeline as pl
    pl.snapshot_close(db)


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

    ok = _send_structured(struct, key="evening_report", min_interval=600)
    if not ok:
        time.sleep(5)  # 网络抖动时重试一次
        ok = _send_structured(struct, key="evening_report", min_interval=600)
    if not ok:
        logger.warning("22:00 盘后报告推送失败或未配置 webhook")
        return "push_failed(webhook unreachable)"
    return "push_ok"


def _pool_trap_scan(db: str, conn) -> None:
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
        Notifier().send_text("\n".join(lines), key="pool_trap", min_interval=3600)


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
    "evening_report": _evening_report,
    "pool_trap_scan": _pool_trap_scan,  # 2026-08-23：候选池杀猪盘扫描
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
    # 晚间盘后报告（2026-08-18 合并 daily_report/P2简报/每日复盘）：22:00 只发一份；
    # 数据滞后时跳过并推送原因（_data_lag_reason 门禁）
    sched.add_job(_wrap("evening_report", _evening_report), CronTrigger(hour=22, minute=0), id="evening_report", misfire_grace_time=7200)
    return sched
