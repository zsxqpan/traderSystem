"""固定飞书问答评测集 + 模拟交易日 E2E（全 mock，不连真实网络）。

验收指标：工具计划正确率、数字可追溯率、实时覆盖率、报告准时送达率、静默失败数。
计分必须真验收：不能 expected_plan=[] / 硬编码 traceable=True / 快乐路径 silent=0。
"""
from __future__ import annotations

import datetime as dt
import re
from unittest import mock

import pytest

from invest.agent.agents import (
    _nums_from_allowed,
    classify_intent,
    plan_tools,
    run_chat,
)
from invest.db import connect, init_db
from invest.scheduler import JobResult, run_job_once

FIVE_REPORT_STATES = frozenset({
    "ok", "generate_failed", "data_insufficient", "send_failed", "rate_limited",
})
_FILING_PROFIT = "8.72"


def _clear_intraday_receipts() -> None:
    """清掉真实库 intraday_report 回执：网关测试经 _invest_db() 写真实库，
    同分钟同 chat 槽位已 succeeded 时去重跳过发送，导致只收 ack 不发报告。"""
    from invest.push import feishu_ws

    try:
        _conn = connect(str(feishu_ws.ROOT / "data" / "invest.db"))
        try:
            _conn.execute("DELETE FROM delivery_receipts WHERE job='intraday_report'")
            _conn.commit()
        finally:
            _conn.close()
    except Exception:
        pass  # 表未建（未跑过 init_db）时静默

EVAL_QA = [
    {
        "id": "realtime_price",
        "text": "600519现价多少",
        "expected_intent": "realtime_quote",
        "expected_plan": ["query_realtime_quote"],
        "must_cite": True,
        "numbers": ["1400"],
    },
    {
        "id": "filing",
        "text": "分析华工科技半年报",
        "expected_intent": "chat",
        "expected_plan": ["web_search"],
        "must_cite": True,
        "numbers": [_FILING_PROFIT],
        "news": True,
    },
    {
        "id": "sector",
        "text": "今天半导体怎么样",
        "expected_intent": "chat",
        "expected_plan": ["cross_validate"],
        "must_cite": False,
        "numbers": [],
    },
    {
        "id": "intraday_report",
        "text": "来一份盘中报告",
        "expected_intent": "intraday_report",
        "expected_plan": [],
        "must_cite": False,
        "numbers": [],
        "gateway": "report",
    },
]


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "invest.db")
    init_db(path)
    return path


def _quote_payload(price: float = 1400.0) -> dict:
    return {
        "obj_type": "stock",
        "quotes": {
            "600519": {
                "name": "贵州茅台",
                "price": price,
                "prev_close": 1390.0,
                "pct": 0.0072,
                "pct_percent": 0.72,
                "pct_unit": "ratio",
                "ts": "2026-08-24T10:00:00",
                "src": "sina",
                "freshness": "live",
                "fallback_level": "none",
                "missing_reason": None,
                "status": "live",
            }
        },
        "coverage": {"requested": 1, "live": 1},
    }


def _news_items() -> list[dict]:
    return [{
        "title": "华工科技2026年半年报",
        "url": "https://example.com/filing",
        "snippet": f"2026-08-20 公司披露半年报，净利润 {_FILING_PROFIT} 亿",
        "published_at": "2026-08-20",
    }]


def _payload_numbers(evidence: list) -> set[str]:
    """只从证据 payload 允许字段抽数字，不含 LLM 自造。"""
    nums: set[str] = set()
    for e in evidence or []:
        nums |= _nums_from_allowed(e.get("data"))
    return nums


def _answer_numbers(answer: str) -> set[str]:
    from invest.agent.agents import _is_year_num, _norm_num

    cleaned = re.sub(r"ev_\d+", "", answer or "")
    out: set[str] = set()
    for m in re.finditer(r"\d+(?:\.\d+)?", cleaned):
        n = m.group(0)
        if _is_year_num(n):
            continue
        out.add(_norm_num(n))
    return out


def numbers_traceable(answer: str, evidence: list, required: list[str]) -> bool:
    """回答中的数字必须来自证据 payload 允许字段，且必含 required。"""
    payload = _payload_numbers(evidence)
    ans = _answer_numbers(answer)
    if "缺口" in (answer or ""):
        return False
    if not re.search(r"ev_\d+", answer or ""):
        return False
    if any(n not in payload or n not in (answer or "") for n in required):
        return False
    return ans <= payload


def collect_unacked_errors(traces: list, *, generate_failed: int = 0) -> int:
    """从 last_trace.errors / generate_failed 汇总未消化异常，禁止写死 0。"""
    n = max(0, generate_failed)
    for t in traces:
        n += len((t or {}).get("errors") or [])
    return n


def count_silent_failures(
    *,
    requested: int,
    results_n: int,
    rate_limit_feedback: bool,
    unacked_errors: int,
) -> int:
    """真实静默：丢行 + 限频无反馈 + last_trace.errors / generate_failed。"""
    silent = max(0, requested - results_n)
    if not rate_limit_feedback:
        silent += 1
    return silent + max(0, unacked_errors)


def score_realtime_coverage(cov: dict) -> float:
    """实时覆盖用 live/requested，不把「不丢行」冒充 live 满分。"""
    req = int(cov.get("requested") or 0)
    if req <= 0:
        return 0.0
    return int(cov.get("live") or 0) / req


def score_on_time(*, window_ok: bool, delivered: bool, n_window: int = 2) -> float:
    """准时只算窗口内成功投递；漏跑不进分子。"""
    if n_window <= 0:
        return 0.0
    return (int(window_ok) + int(delivered)) / n_window


def score_traceable_rate(rows: list) -> float:
    """must_cite=False / 网关题不进可追溯分母。"""
    cite = [r for r in rows if r.get("must_cite") and not r.get("gateway")]
    if not cite:
        return 0.0
    return sum(1 for r in cite if r.get("traceable")) / len(cite)


def _intraday_struct(text: str = "【盘面总览】模拟盘中报告", status: str = "ok") -> dict:
    return {
        "title": "盘中报告",
        "sections": [{"type": "text", "text": text}],
        "completeness": {"status": status, "detail": "" if status == "ok" else status},
    }


class _PlannedLLM:
    """计划驱动意图：模型只组织答案，引用已执行证据。"""

    def __init__(self, *a, **k):
        self.last_trace = {}

    def run(self, system, user, tools, dispatch, job, max_turns, history=None, **kw):
        return "茅台现价 1400 元 [ev_1]"


class _HonestChatLLM:
    """普通问答：引用已执行计划证据里的 payload 数字，不自造。"""

    def __init__(self, *a, **k):
        self.last_trace = {}

    def run(self, system, user, tools, dispatch, job, max_turns, history=None, **kw):
        text = user or ""
        if "半年报" in text:
            return f"华工科技半年报净利润 {_FILING_PROFIT} 亿 [ev_1]"
        return "半导体板块情绪中性，等待主线确认。"


class _InventProfitLLM:
    """假绿对照：自造 10 亿再挂 ev，enforce 必须剥离。"""

    def __init__(self, *a, **k):
        self.last_trace = {}

    def run(self, system, user, tools, dispatch, job, max_turns, history=None, **kw):
        return "华工科技半年报净利润 10 亿 [ev_1]"


def _run_qa_case(conn, case: dict) -> dict:
    text = case["text"]
    intent = classify_intent(text)
    plan = [s.get("name") for s in plan_tools(text, intent)]
    plan_ok = plan == case["expected_plan"] and intent == case["expected_intent"]
    if case.get("gateway") == "report":
        return {
            "id": case["id"],
            "plan_ok": plan_ok,
            "traceable": None,
            "cited": False,
            "answer": "",
            "gateway": True,
            "must_cite": bool(case.get("must_cite")),
            "actual_tools": [],
            "trace": {},
        }

    import invest.agent.agents as agents_mod

    llm_cls = _PlannedLLM if intent == "realtime_quote" else _HonestChatLLM
    with mock.patch.object(agents_mod, "LLMClient", llm_cls), \
         mock.patch("invest.agent.tools.query_realtime_quote", return_value=_quote_payload()), \
         mock.patch("invest.agent.web_tools.web_search", return_value=_news_items()):
        answer = run_chat(conn, text, job="eval")
    trace = dict(agents_mod.last_trace or {})
    evidence = list(trace.get("evidence") or [])
    actual_tools = list(trace.get("actual_tools") or [])
    must_cite = bool(case.get("must_cite"))
    if must_cite:
        cited = "缺口" not in answer and bool(re.search(r"ev_\d+", answer))
        traceable = numbers_traceable(answer, evidence, case.get("numbers") or [])
    else:
        cited = False
        traceable = None
    return {
        "id": case["id"],
        "plan_ok": plan_ok,
        "traceable": traceable,
        "cited": cited,
        "answer": answer,
        "trace": trace,
        "actual_tools": actual_tools,
        "must_cite": must_cite,
    }


def test_eval_silent_counter_detects_drop_and_mute_ratelimit():
    """静默计数器必须能抓住丢行/限频无反馈；不能恒为 0。"""
    assert count_silent_failures(
        requested=3, results_n=2, rate_limit_feedback=True,
        unacked_errors=collect_unacked_errors([{}]),
    ) == 1
    assert count_silent_failures(
        requested=3, results_n=3, rate_limit_feedback=False,
        unacked_errors=collect_unacked_errors([{}]),
    ) == 1
    assert count_silent_failures(
        requested=3, results_n=3, rate_limit_feedback=True,
        unacked_errors=collect_unacked_errors([{"errors": ["a", "b"]}]),
    ) == 2
    assert count_silent_failures(
        requested=3, results_n=3, rate_limit_feedback=True,
        unacked_errors=collect_unacked_errors([{"errors": []}]),
    ) == 0


def test_eval_silent_counts_trace_errors_and_generate_failed():
    """静默必须读 last_trace.errors / generate_failed，不能 unacked_errors=0 写死。"""
    traces = [{"errors": ["tool boom"]}, {"errors": []}]
    assert collect_unacked_errors(traces, generate_failed=1) == 2
    assert count_silent_failures(
        requested=3, results_n=3, rate_limit_feedback=True,
        unacked_errors=collect_unacked_errors(traces, generate_failed=1),
    ) == 2


def test_eval_coverage_uses_live_not_row_count():
    """部分失败：不丢行 ≠ 实时覆盖满分。"""
    cov = {"requested": 3, "live": 2}
    assert score_realtime_coverage(cov) == pytest.approx(2 / 3)
    assert score_realtime_coverage(cov) != 1.0


def test_eval_on_time_excludes_missed():
    """漏跑处理正确不得计入准时送达。"""
    assert score_on_time(window_ok=True, delivered=True) == 1.0
    assert score_on_time(window_ok=False, delivered=False) == 0.0
    padded = (int(False) + int(True) + int(False)) / 3
    assert padded != 0.0
    assert score_on_time(window_ok=False, delivered=False) != padded


def test_eval_qa_tool_plan_and_number_traceability(db_path: str):
    """固定评测集：财报/板块必须有应有工具计划；数字必须来自 payload 字段。"""
    conn = connect(db_path)
    try:
        scored = [_run_qa_case(conn, case) for case in EVAL_QA]
    finally:
        conn.close()
    plan_hits = sum(1 for r in scored if r["plan_ok"])
    plan_acc = plan_hits / len(EVAL_QA)
    trace_acc = score_traceable_rate(scored)
    assert plan_acc == 1.0, scored
    assert trace_acc == 1.0, scored
    realtime = next(r for r in scored if r["id"] == "realtime_price")
    assert "1400" in realtime["answer"]
    assert "query_realtime_quote" in realtime["actual_tools"]
    filing = next(r for r in scored if r["id"] == "filing")
    assert _FILING_PROFIT in filing["answer"]
    assert "10" not in _answer_numbers(filing["answer"])
    assert "缺口" not in filing["answer"]
    assert "web_search" in filing["actual_tools"]
    sector = next(r for r in scored if r["id"] == "sector")
    assert sector["plan_ok"] is True
    assert sector["traceable"] is None
    assert "cross_validate" in sector["actual_tools"]


def test_eval_invented_profit_is_stripped(db_path: str):
    """LLM 自造 10 亿再挂 ev：数字不得留在答案里，也不能算可追溯。"""
    import invest.agent.agents as agents_mod

    conn = connect(db_path)
    try:
        with mock.patch.object(agents_mod, "LLMClient", _InventProfitLLM), \
             mock.patch("invest.agent.web_tools.web_search", return_value=_news_items()):
            answer = run_chat(conn, "分析华工科技半年报", job="eval")
        evidence = list((agents_mod.last_trace or {}).get("evidence") or [])
        actual = list((agents_mod.last_trace or {}).get("actual_tools") or [])
    finally:
        conn.close()
    assert "web_search" in actual
    assert "10" not in _answer_numbers(answer)
    assert numbers_traceable(answer, evidence, [_FILING_PROFIT]) is False


def test_eval_realtime_partial_source_full_coverage(db_path: str):
    """数据源部分失败：每个请求标的都有状态，不得静默丢行。"""
    from invest.data.quotes import get_quotes, summarize_coverage
    from invest.data.realtime import Quote

    now = dt.datetime.now()
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO daily_bars(symbol, date, close, src) VALUES('000002','2026-08-21',10.0,'akshare')"
        )
        conn.commit()
    finally:
        conn.close()

    def _sina(session, symbols):
        return {"sh600519": Quote(symbol="sh600519", price=1400.0, pct=0.01, ts=now, src="sina")}

    def _tencent(session, symbols):
        out = {}
        for s in symbols:
            bare = s[2:] if s[:2] in ("sh", "sz", "bj") else s
            if bare == "000001":
                out["sz000001"] = Quote(symbol="sz000001", price=11.0, pct=0.02, ts=now, src="tencent")
        return out

    with mock.patch("invest.data.realtime._fetch_sina", side_effect=_sina), \
         mock.patch("invest.data.realtime._fetch_tencent", side_effect=_tencent), \
         mock.patch("invest.data.realtime._fetch_em", return_value={}):
        results = get_quotes(
            ["600519", "000001", "000002"], obj_type="stock", db_path=db_path, now=now,
        )

    assert [r.ref.symbol for r in results] == ["600519", "000001", "000002"]
    by = {r.ref.symbol: r for r in results}
    assert by["600519"].status == "live" and by["600519"].src == "sina"
    assert by["000001"].status == "live" and by["000001"].src == "tencent"
    assert by["000002"].status in ("fallback", "missing")
    cov = summarize_coverage(results)
    assert cov["requested"] == 3
    assert cov["live"] == 2
    assert cov["coverage"] >= 2 / 3
    silent_dropped = 3 - cov["requested"]
    assert silent_dropped == 0
    assert count_silent_failures(
        requested=3, results_n=len(results), rate_limit_feedback=True,
        unacked_errors=collect_unacked_errors([{}]),
    ) == 0


def _feishu_event(text: str, mentions=("ou_bot",)):
    from tests.test_feishu_ws import _event

    return _event("ou_owner", text, mentions=list(mentions))


def _score_intraday_gateway(result, struct: dict, sent: list[str]) -> dict:
    """盘中网关：必须看完整性/五态，禁止硬编码 traceable=True。"""
    status = getattr(result, "status", None) if result is not None else None
    gate = (struct or {}).get("completeness") or {}
    gate_status = gate.get("status")
    five_ok = status in FIVE_REPORT_STATES
    complete_ok = gate_status in FIVE_REPORT_STATES
    delivered = any("模拟盘中报告" in (s or "") for s in sent)
    traceable = bool(five_ok and complete_ok and status == "ok" and gate_status == "ok" and delivered)
    return {
        "status": status,
        "completeness": gate_status,
        "five_ok": five_ok,
        "traceable": traceable,
        "delivered": delivered,
    }


def test_eval_intraday_report_gateway_delivers(db_path, monkeypatch):
    """盘中报告：网关按时送达，限频给反馈，不静默丢；断言完整性/五态。"""
    from invest.push import feishu_ws
    from tests.test_feishu_ws import FakeSettings

    sent: list[str] = []
    struct = _intraday_struct()
    monkeypatch.setattr(feishu_ws, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(feishu_ws, "_bot_open_id_cache", "ou_bot")
    monkeypatch.setattr("invest.push.feishu_push.send_card", lambda *a, **k: False)
    monkeypatch.setattr(
        "invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: struct)
    feishu_ws._last_reply_at.clear()
    _clear_intraday_receipts()

    feishu_ws._handle_event(_feishu_event("来一份盘中报告"))
    scored = _score_intraday_gateway(feishu_ws._last_report_result, struct, sent)
    assert scored["five_ok"] is True
    assert scored["completeness"] == "ok"
    assert scored["traceable"] is True
    assert scored["delivered"] is True
    assert feishu_ws._last_report_result.status in FIVE_REPORT_STATES

    feishu_ws._handle_event(_feishu_event("再来一份盘中报告"))
    assert any(k in sent[-1] for k in ("频繁", "稍后", "限频", "间隔"))
    assert feishu_ws._last_report_result.status == "rate_limited"
    assert feishu_ws._last_report_result.status in FIVE_REPORT_STATES


def test_eval_intraday_insufficient_not_hardcoded_traceable(db_path, monkeypatch):
    """数据不足不得硬编码 traceable=True。"""
    from invest.push import feishu_ws
    from tests.test_feishu_ws import FakeSettings

    sent: list[str] = []
    struct = _intraday_struct("数据不足", status="data_insufficient")
    monkeypatch.setattr(feishu_ws, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(feishu_ws, "_bot_open_id_cache", "ou_bot")
    monkeypatch.setattr("invest.push.feishu_push.send_card", lambda *a, **k: False)
    monkeypatch.setattr(
        "invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: struct)
    feishu_ws._last_reply_at.clear()
    feishu_ws._handle_event(_feishu_event("来一份盘中报告"))
    scored = _score_intraday_gateway(feishu_ws._last_report_result, struct, sent)
    assert feishu_ws._last_report_result.status == "data_insufficient"
    assert scored["traceable"] is False
    assert scored["five_ok"] is True


def test_eval_simulated_trading_day_auction_and_jobs(db_path: str):
    """模拟交易日：竞价准时送达；漏跑只记 missed+告警，不拿盘中数据补发。漏跑必须进指标。"""
    from invest import scheduler

    on_time = {}
    delivered = 0
    expected = 0

    now_ok = dt.datetime(2026, 8, 24, 9, 27)
    channels = {"feishu": "succeeded", "wecom": "succeeded"}
    with mock.patch("invest.pipeline.notify_auction", return_value=channels), \
         mock.patch("invest.data.calendar.is_trading_day", return_value=True):
        result = run_job_once("auction", db_path=db_path, now=now_ok)
    expected += 1
    window_ok = result.status == "ok" and result.channel_results.get("feishu") == "succeeded"
    if window_ok:
        delivered += 1
    on_time["auction"] = result.status
    # 漏跑另计，不进 expected / delivered

    late = dt.datetime(2026, 8, 24, 10, 5)
    task = mock.Mock(return_value=JobResult.ok("must not backfill"))
    with mock.patch.dict(scheduler.JOB_FUNCS, {"auction": task}, clear=True), \
         mock.patch("invest.data.calendar.is_trading_day", return_value=True):
        already = scheduler.run_compensation_scan(
            db_path=db_path, now=late, jobs={"auction"},
        )
    assert already["auction"].status == "already_ok"
    task.assert_not_called()

    miss_db = db_path + ".miss"
    init_db(miss_db)
    with mock.patch.dict(scheduler.JOB_FUNCS, {"auction": task}, clear=True), \
         mock.patch("invest.data.calendar.is_trading_day", return_value=True), \
         mock.patch("invest.scheduler.Notifier") as notifier:
        notifier.return_value.send_text.return_value = {"wecom": True}
        missed = scheduler.run_compensation_scan(
            db_path=miss_db, now=late, jobs={"auction"},
        )
    assert missed["auction"].status == "missed"
    task.assert_not_called()
    notifier.return_value.send_text.assert_called()
    missed_handled = (
        missed["auction"].status == "missed"
        and notifier.return_value.send_text.called
        and not task.called
    )
    on_time["auction_missed"] = missed["auction"].status

    on_time_rate = delivered / expected
    silent = count_silent_failures(
        requested=expected, results_n=delivered, rate_limit_feedback=True,
        unacked_errors=collect_unacked_errors([]),
    )
    assert on_time_rate == 1.0
    assert missed_handled
    assert silent == 0
    assert on_time["auction"] == "ok"
    assert on_time["auction_missed"] == "missed"


def _partial_quotes(db_path: str):
    from invest.data.quotes import get_quotes, summarize_coverage
    from invest.data.realtime import Quote

    now = dt.datetime.now()
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO daily_bars(symbol, date, close, src) VALUES('000002','2026-08-21',10.0,'akshare')"
        )
        conn.commit()
    finally:
        conn.close()

    def _sina(session, symbols):
        return {"sh600519": Quote(symbol="sh600519", price=1400.0, pct=0.01, ts=now, src="sina")}

    def _tencent(session, symbols):
        return {
            "sz000001": Quote(symbol="sz000001", price=11.0, pct=0.02, ts=now, src="tencent"),
        }

    with mock.patch("invest.data.realtime._fetch_sina", side_effect=_sina), \
         mock.patch("invest.data.realtime._fetch_tencent", side_effect=_tencent), \
         mock.patch("invest.data.realtime._fetch_em", return_value={}):
        quotes = get_quotes(
            ["600519", "000001", "000002"], obj_type="stock", db_path=db_path, now=now,
        )
    cov = summarize_coverage(quotes)
    return quotes, cov


def test_eval_acceptance_bundle(db_path: str, monkeypatch):
    """五项验收指标汇总：部分失败/漏跑/限频反馈都进指标，快乐路径不硬编码 silent=0。"""
    conn = connect(db_path)
    try:
        qa = [_run_qa_case(conn, case) for case in EVAL_QA]
    finally:
        conn.close()

    plan_acc = sum(1 for r in qa if r["plan_ok"]) / len(qa)
    trace_acc = score_traceable_rate(qa)

    quotes, cov = _partial_quotes(db_path)
    assert cov["requested"] == 3
    assert len(quotes) == cov["requested"]
    realtime_cov = score_realtime_coverage(cov)

    from invest.push import feishu_ws
    from tests.test_feishu_ws import FakeSettings

    sent: list[str] = []
    struct = _intraday_struct()
    monkeypatch.setattr(feishu_ws, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(feishu_ws, "_bot_open_id_cache", "ou_bot")
    monkeypatch.setattr("invest.push.feishu_push.send_card", lambda *a, **k: False)
    monkeypatch.setattr(
        "invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: struct)
    feishu_ws._last_reply_at.clear()
    _clear_intraday_receipts()
    feishu_ws._handle_event(_feishu_event("来一份盘中报告"))
    gw = _score_intraday_gateway(feishu_ws._last_report_result, struct, sent)
    feishu_ws._handle_event(_feishu_event("再来一份盘中报告"))
    rate_fb = any(k in (sent[-1] or "") for k in ("频繁", "稍后", "限频", "间隔"))
    assert feishu_ws._last_report_result.status == "rate_limited"

    now_ok = dt.datetime(2026, 8, 24, 9, 27)
    with mock.patch("invest.pipeline.notify_auction", return_value={"feishu": "succeeded"}), \
         mock.patch("invest.data.calendar.is_trading_day", return_value=True):
        auction = run_job_once("auction", db_path=db_path, now=now_ok)

    from invest import scheduler

    late = dt.datetime(2026, 8, 24, 10, 5)
    task = mock.Mock(return_value=JobResult.ok("must not backfill"))
    miss_db = db_path + ".miss"
    init_db(miss_db)
    with mock.patch.dict(scheduler.JOB_FUNCS, {"auction": task}, clear=True), \
         mock.patch("invest.data.calendar.is_trading_day", return_value=True), \
         mock.patch("invest.scheduler.Notifier") as notifier:
        notifier.return_value.send_text.return_value = {"wecom": True}
        missed = scheduler.run_compensation_scan(
            db_path=miss_db, now=late, jobs={"auction"},
        )
    missed_ok = (
        missed["auction"].status == "missed"
        and notifier.return_value.send_text.called
        and not task.called
    )
    window_ok = auction.status == "ok" and auction.channel_results.get("feishu") == "succeeded"
    on_time = score_on_time(window_ok=window_ok, delivered=gw["delivered"])

    traces = [r.get("trace") for r in qa if r.get("trace") is not None]
    generate_failed = int((gw.get("status") or "") == "generate_failed")
    silent = count_silent_failures(
        requested=cov["requested"],
        results_n=len(quotes),
        rate_limit_feedback=rate_fb,
        unacked_errors=collect_unacked_errors(traces, generate_failed=generate_failed),
    )

    metrics = {
        "工具计划正确率": plan_acc,
        "数字可追溯率": trace_acc,
        "实时覆盖率": realtime_cov,
        "报告准时送达率": on_time,
        "静默失败数": silent,
    }
    assert metrics["工具计划正确率"] == 1.0, (metrics, qa)
    assert metrics["数字可追溯率"] == 1.0, (metrics, qa)
    assert metrics["实时覆盖率"] == pytest.approx(2 / 3), metrics
    assert cov["live"] == 2 and cov["requested"] == 3
    assert all(getattr(q, "status", None) for q in quotes)
    assert metrics["报告准时送达率"] == 1.0, metrics
    assert missed_ok
    assert missed["auction"].status == "missed"
    assert metrics["静默失败数"] == 0, metrics
    assert gw["traceable"] is True
    assert gw["completeness"] == "ok"
