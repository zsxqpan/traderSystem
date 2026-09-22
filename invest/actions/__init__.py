"""动作清单：规则合成 → daily_actions → 报告/仪表盘消费。"""
from __future__ import annotations

from invest.actions.compose import compose
from invest.actions.format import format_actions, pick_b1
from invest.actions.persist import (
    mark_action,
    persist_actions,
    update_statuses,
)
from invest.actions.query import list_actions
from invest.actions.types import Action

__all__ = [
    "Action",
    "compose",
    "format_actions",
    "list_actions",
    "mark_action",
    "persist_actions",
    "pick_b1",
    "update_statuses",
]
