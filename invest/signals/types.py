"""交易信号数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field

SESSIONS = ("auction", "intraday", "close", "daily")
HORIZONS = ("short", "mid")
LAYERS = ("watch", "discovery", "market")
SEVERITIES = ("info", "watch", "action")


@dataclass
class Signal:
    id: str
    name: str
    session: str
    severity: str
    subject_type: str
    subject: str
    hint: str
    evidence: dict = field(default_factory=dict)
    horizon: str = "short"
    layer: str = "watch"
