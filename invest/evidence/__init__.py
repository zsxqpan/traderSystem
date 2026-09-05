"""中期证据驾驶舱：结构化 FactCard，不含综合买卖排名。"""
from __future__ import annotations

from invest.evidence.factcards import (
    DIMENSIONS,
    MAX_DEEP_INDUSTRIES,
    MAX_DEEP_STOCKS,
    RULE_VERSION,
    FactCard,
    build_industry_card,
    build_stock_card,
    deep_dive,
    detect_important_changes,
    discover_industries,
    extract_recent_facts,
    format_change_digest,
    load_card,
    load_comparison,
    lookup_evidence,
    persist_card,
    record_comparison,
    run_factcard_refresh,
)

__all__ = [
    "DIMENSIONS",
    "MAX_DEEP_INDUSTRIES",
    "MAX_DEEP_STOCKS",
    "RULE_VERSION",
    "FactCard",
    "build_industry_card",
    "build_stock_card",
    "deep_dive",
    "detect_important_changes",
    "discover_industries",
    "extract_recent_facts",
    "format_change_digest",
    "load_card",
    "load_comparison",
    "lookup_evidence",
    "persist_card",
    "record_comparison",
    "run_factcard_refresh",
]
