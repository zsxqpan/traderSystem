"""报告投递管道：清单校验 → 生成 → 发送前完整性 → 逐通道回执。

复用任务 1 的 JobResult / delivery_receipts，不另起账本。
"""
from __future__ import annotations

import datetime as dt
import inspect
import time

from invest.scheduler import JobResult
from invest.skills import snapshot as snapshot_mod
from invest.skills.contract import check_completeness, get_manifest
from invest.skills.registry import get as get_skill

REPORT_OUTCOMES = (
    "generate_failed",
    "data_insufficient",
    "send_failed",
    "rate_limited",
    "ok",
)


def _call_render(skill_id: str, db_path: str, snapshot, **params):
    """只在签名明确不支持 snapshot 时省略该参数；体内 TypeError 原样上抛。"""
    mod = get_skill(skill_id)
    render = mod.render
    kwargs = dict(params)
    kwargs["db_path"] = db_path
    try:
        sig = inspect.signature(render)
        accept_snapshot = "snapshot" in sig.parameters or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
    except (TypeError, ValueError):
        accept_snapshot = True
    if accept_snapshot:
        kwargs["snapshot"] = snapshot
    return render(**kwargs)


def deliver_report(
    skill_id: str,
    db_path: str,
    *,
    send_fn=None,
    rate_limited: bool = False,
    now: dt.datetime | None = None,
    snapshot=None,
    **render_params,
) -> JobResult:
    """生成并（可选）发送一份报告，返回可区分的 JobResult。"""
    now = now or dt.datetime.now()
    if rate_limited:
        return JobResult("rate_limited", "报告请求被限频", artifact=skill_id)

    manifest = get_manifest(skill_id)
    snap = snapshot or snapshot_mod.freeze_snapshot(skill_id, db_path, now=now)
    gate = check_completeness(manifest, snap)
    if not gate.ok:
        return JobResult(gate.status, gate.detail, artifact=skill_id)

    t0 = time.monotonic()
    try:
        struct = _call_render(skill_id, db_path, snap, **render_params)
    except Exception as exc:
        return JobResult("generate_failed", str(exc), artifact=skill_id)
    elapsed = time.monotonic() - t0
    if elapsed > manifest.max_seconds:
        return JobResult("generate_failed", "超过最大生成时长", artifact=skill_id)
    if not isinstance(struct, dict) or not struct.get("sections"):
        return JobResult("generate_failed", "报告结构为空", artifact=skill_id)

    if send_fn is None:
        return JobResult.ok(gate.detail, artifact=skill_id)

    try:
        ok = bool(send_fn(struct))
    except Exception as exc:
        return JobResult("send_failed", str(exc), artifact=skill_id)
    if not ok:
        return JobResult(
            "send_failed", "发送失败", artifact=skill_id,
            channel_results={"delivery": "failed"},
        )
    return JobResult.ok(gate.detail, artifact=skill_id)
