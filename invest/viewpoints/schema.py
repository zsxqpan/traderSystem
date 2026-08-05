"""观点五要素校验与枚举。"""
from __future__ import annotations

SOURCES = ("research", "trade", "arbiter", "user")
PERIOD_TAGS = ("micro", "short", "mid", "long")
STATUSES = (
    "draft", "active", "verifying", "verified",
    "expired", "pending_review", "updated", "invalidated",
)


def validate_viewpoint(data: dict) -> None:
    """强制五要素：结论 / 周期标签 / 置信度 / 依据链 / 失效条件。"""
    missing = []
    if not str(data.get("conclusion", "")).strip():
        missing.append("conclusion")
    if data.get("period_tag") not in PERIOD_TAGS:
        missing.append("period_tag")
    conf = data.get("confidence")
    if conf is None or not (0 <= float(conf) <= 1):
        missing.append("confidence")
    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        missing.append("evidence")
    if not str(data.get("invalid_condition", "")).strip():
        missing.append("invalid_condition")
    if missing:
        raise ValueError(f"观点缺少必要要素: {missing}")