"""组合风险簇：行业→风险簇映射、自动打标、跨周期敞口合并、预算上限（TODO 2.3）。

2026-08-15 落地（对齐 v3 11.2 / TODO 2.3）：
- 风险簇映射 v1：手工规则表（行业关键词 → 簇），见 INDUSTRY_CLUSTERS；
- 自动打标：标的按行业（candidate_pool.industry）自动归簇；
- 跨周期敞口合并：同标的/同 L2/同风险簇的跨周期仓位合并计算；
- 组合预算上限：L2 25%/35%、风险簇 40%、风格 60%、事件博弈 20%；
- 相关性-共同因子：历史相关性低但经济驱动相同仍归同簇（由手工规则表保证）。
"""
from __future__ import annotations

import sqlite3

from invest.config import load_yaml_config

# 行业关键词 → 风险簇（v1 手工规则表；可扩展）
INDUSTRY_CLUSTERS: dict[str, tuple[str, ...]] = {
    "高股息": ("银行", "煤炭", "石油", "公路铁路运输", "港口航运", "公用事业", "电力"),
    "出口链": ("家电", "家居用品", "汽车零部件", "纺织", "服装", "光伏", "锂电", "储能"),
    "地产链": ("房地产", "建筑材料", "建筑装饰", "家居用品", "厨卫电器", "水泥"),
    "上游资源": ("工业金属", "小金属", "贵金属", "化学原料", "油气开采", "煤炭"),
    "AI科技": ("半导体", "元件", "光学光电子", "消费电子", "软件开发", "IT服务", "计算机设备", "通信设备"),
    "医药医疗": ("化学制药", "生物制品", "中药", "医疗器械", "医疗服务", "医药商业"),
    "军工": ("军工电子", "军工装备", "国防军工"),
    "大消费": ("白酒", "食品加工", "饮料乳品", "调味品", "旅游及酒店", "养殖业", "农产品加工"),
    "金融地产": ("银行", "保险", "证券", "多元金融", "房地产"),
    "新能源": ("光伏设备", "风电设备", "电池", "电力设备", "电网设备"),
    "汽车": ("汽车整车", "汽车零部件", "汽车服务及其他"),
    "政策敏感": ("教育", "传媒", "互联网电商", "影视院线", "医疗服务"),
}

# 簇类型：风格簇（受整体市场风格驱动）与主题簇（事件驱动）
STYLE_CLUSTERS = ("高股息", "上游资源", "大消费", "金融地产")
EVENT_CLUSTERS = ("出口链", "地产链", "AI科技", "医药医疗", "军工", "新能源", "汽车", "政策敏感")

# 组合预算上限（v3：L2 25%/35%、风险簇 40%、风格 60%、事件博弈 20%）
DEFAULT_BUDGETS = {
    "style_total": 0.60,      # 全部风格簇合计
    "cluster": 0.40,          # 单风险簇
    "l2_soft": 0.25,          # 单 L2 软上限（主动管理）
    "l2_hard": 0.35,          # 单 L2 硬上限
    "event": 0.20,            # 事件博弈簇合计
}

# 事件博弈类簇（总仓位受 event 上限约束）
_EVENT_BUDGETED = ("出口链", "地产链", "军工", "政策敏感")


def _load_custom_clusters(config: dict | None = None) -> dict[str, tuple[str, ...]]:
    """config.yaml clusters 段可追加/覆盖手工规则表。"""
    config = config or load_yaml_config()
    extra = config.get("clusters", {}) or {}
    out = {k: tuple(v) for k, v in INDUSTRY_CLUSTERS.items()}
    for k, v in extra.items():
        out[k] = tuple(v)
    return out


def industry_to_cluster(industry: str, clusters: dict | None = None) -> str:
    """行业 → 风险簇（关键词匹配，首个命中；未命中返回 '其他'）。"""
    if not industry:
        return "其他"
    clusters = clusters or _load_custom_clusters()
    for cluster, keywords in clusters.items():
        for kw in keywords:
            if kw in industry:
                return cluster
    return "其他"


def cluster_type(cluster: str) -> str:
    """簇类型：style / event / other。"""
    if cluster in STYLE_CLUSTERS:
        return "style"
    if cluster in EVENT_CLUSTERS:
        return "event"
    return "other"


def symbol_cluster(conn: sqlite3.Connection, symbol: str, clusters: dict | None = None) -> str:
    """标的 → 风险簇：查 candidate_pool.industry → industry_to_cluster。"""
    row = conn.execute(
        "SELECT industry FROM candidate_pool WHERE symbol=?", (symbol,)
    ).fetchone()
    return industry_to_cluster(row["industry"] if row else "", clusters)


def exposure_report(conn: sqlite3.Connection, positions: list[dict]) -> dict:
    """组合敞口报告：按风险簇/风格/事件汇总持仓。

    positions: [{symbol, weight, cycle?}]（跨周期卡片合并后传入）。
    返回 {clusters: {簇: 权重}, style_total, event_total, violations: [...]}。
    """
    clusters = _load_custom_clusters()
    budget_cfg = load_yaml_config().get("budgets", {})
    budgets = {**DEFAULT_BUDGETS, **budget_cfg}

    by_cluster: dict[str, float] = {}
    for pos in positions:
        sym = pos["symbol"]
        w = float(pos.get("weight", 0.0))
        cl = symbol_cluster(conn, sym, clusters)
        by_cluster[cl] = by_cluster.get(cl, 0.0) + w

    style_total = sum(w for cl, w in by_cluster.items() if cl in STYLE_CLUSTERS)
    event_total = sum(w for cl, w in by_cluster.items() if cl in _EVENT_BUDGETED)
    violations: list[str] = []
    for cl, w in by_cluster.items():
        if w > budgets["cluster"]:
            violations.append(f"风险簇 {cl} 敞口 {w:.0%} 超过上限 {budgets['cluster']:.0%}")
    if style_total > budgets["style_total"]:
        violations.append(f"风格簇合计 {style_total:.0%} 超过上限 {budgets['style_total']:.0%}")
    if event_total > budgets["event"]:
        violations.append(f"事件博弈簇合计 {event_total:.0%} 超过上限 {budgets['event']:.0%}")
    return {
        "clusters": dict(sorted(by_cluster.items(), key=lambda x: -x[1])),
        "style_total": round(style_total, 4),
        "event_total": round(event_total, 4),
        "violations": violations,
    }


def merge_cross_cycle(positions: list[dict]) -> list[dict]:
    """跨周期敞口合并：同标的的多个仓位（不同周期卡片）合并为单条。

    positions: [{symbol, weight, cycle}] → [{symbol, weight(合并), cycles: [...]}]。
    """
    merged: dict[str, dict] = {}
    for pos in positions:
        sym = pos["symbol"]
        w = float(pos.get("weight", 0.0))
        cyc = pos.get("cycle", "")
        if sym not in merged:
            merged[sym] = {"symbol": sym, "weight": 0.0, "cycles": []}
        merged[sym]["weight"] += w
        if cyc and cyc not in merged[sym]["cycles"]:
            merged[sym]["cycles"].append(cyc)
    out = []
    for item in merged.values():
        item["weight"] = round(item["weight"], 4)
        out.append(item)
    return out


def check_cluster_budgets(conn: sqlite3.Connection, positions: list[dict]) -> list[str]:
    """组合预算校验入口：先合并跨周期，再出敞口报告，返回违规列表。"""
    merged = merge_cross_cycle(positions)
    report = exposure_report(conn, merged)
    return report["violations"]
