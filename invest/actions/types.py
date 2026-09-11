"""动作清单数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field

VERBS = ("buy", "add", "hold", "reduce", "sell", "wait", "watch")
SOURCES = ("card", "plan", "pool", "signal", "llm", "user")
STATUSES = ("pending", "triggered", "expired", "done", "skipped")

VERB_CN = {
    "buy": "买",
    "add": "加",
    "hold": "持",
    "reduce": "减",
    "sell": "卖",
    "wait": "等",
    "watch": "看",
}


@dataclass
class Action:
    date: str
    symbol: str
    name: str = ""
    verb: str = "hold"
    priority: int = 2
    source: str = "pool"
    source_ref: str = ""
    entry_lo: float | None = None
    entry_hi: float | None = None
    stop_loss: float | None = None
    target: float | None = None
    invalid_condition: str = ""
    position_hint: str = ""
    status: str = "pending"
    hint: str = ""
    evidence: dict = field(default_factory=dict)
