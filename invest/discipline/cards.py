"""机会卡片（TODO 1.3，2026-08-15）：模板、状态机、容量、赔率。

卡片模板（v3 10.1 固定字段 + v2 5.4 精简版）：
symbol / level / cycle / spread_type / spread_value / spread_pct / z_score /
regression_anchor / thesis / falsify / entry_range / stop_loss / target / reward_risk / status

状态机：candidate → locked → review → downgraded / void
- candidate: 建卡（可编辑）；
- locked:    三句话验证通过后锁定（A/B、周期、主价差、止损不可改）；
- review:    复评中（周期漂移/环境变化触发）；
- downgraded/void: 降级/作废。

容量：活跃卡片（非 void/downgraded）<= CARD_LIMIT(20)，B 级以上入卡须淘汰最弱一张。

赔率计算（刚性顺序）：证伪 → 止损 → 入场 → 目标 → 成本 → R
R = |入场-止损|；赔率 = (目标-入场-成本) / (入场-止损+成本)
"""
from __future__ import annotations

import sqlite3

CARD_LIMIT = 20
STATUSES = ("candidate", "locked", "review", "downgraded", "void")
LEVEL_RANK = {"S": 0, "A": 1, "B": 2, "C": 3}


def create_card(
    conn: sqlite3.Connection,
    symbol: str,
    level: str = "B",
    cycle: str = "short",
    spread_type: str = "",
    spread_value: str = "",
    thesis: str = "",
    falsify: str = "",
    entry_range: str = "",
    stop_loss: float | None = None,
    target: float | None = None,
) -> dict:
    """建卡（candidate 状态）。校验：标的在候选池、三句话验证非空、容量上限。"""
    if level not in LEVEL_RANK:
        raise ValueError(f"卡片等级必须为 {list(LEVEL_RANK)}")
    # 标的必须在候选池
    row = conn.execute(
        "SELECT symbol FROM candidate_pool WHERE symbol=? AND out_date IS NULL", (symbol,)
    ).fetchone()
    if row is None:
        raise ValueError(f"{symbol} 不在候选池，禁止建卡")
    # 三句话验证（thesis 必须有实质内容）
    if not thesis or len(thesis.strip()) < 10:
        raise ValueError("三句话验证必须写清逻辑（不少于10字），讲不清不准入")
    # 容量上限：活跃卡片（S/A/B 级）
    active = conn.execute(
        """SELECT * FROM cards WHERE status IN ('candidate','locked','review')""".replace("cards", "cards")
    ).fetchall()
    active_b = [r for r in active if LEVEL_RANK.get(r["level"], 99) <= LEVEL_RANK["B"]]
    if len(active_b) >= CARD_LIMIT:
        raise ValueError(f"活跃卡片已满（上限 {CARD_LIMIT}），须先淘汰最弱一张")
    cur = conn.execute(
        """INSERT INTO cards(symbol, level, cycle, spread_type, spread_value, thesis, falsify,
                             entry_range, stop_loss, target, status, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?, 'candidate', datetime('now','localtime'))""",
        (symbol, level, cycle, spread_type, spread_value, thesis, falsify,
         entry_range, stop_loss, target),
    )
    conn.commit()
    return {"card_id": cur.lastrowid, "symbol": symbol, "status": "candidate"}


def compute_rr(
    entry: float,
    stop_loss: float,
    target: float,
    cost: float = 0.0,
) -> float:
    """赔率（盈亏比）：(目标-入场-成本) / (入场-止损+成本)。"""
    risk = abs(entry - stop_loss) + cost
    if risk <= 0:
        return 0.0
    reward = target - entry - cost
    return round(max(0.0, reward / risk), 2)


def validate_card(conn: sqlite3.Connection, card_id: int) -> list[str]:
    """卡片完整性校验：证伪/止损/入场/目标/赔率刚性顺序（v3 10.1）。"""
    row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    if row is None:
        return ["卡片不存在"]
    problems: list[str] = []
    if not (row["falsify"] or "").strip():
        problems.append("缺证伪条件")
    if row["stop_loss"] is None:
        problems.append("缺止损位")
    if not (row["entry_range"] or "").strip():
        problems.append("缺入场区间")
    if row["target"] is None:
        problems.append("缺目标价")
    if row["stop_loss"] is not None and row["target"] is not None:
        if row["stop_loss"] >= row["target"]:
            problems.append("止损 >= 目标，赔率必然 <=0")
    return problems


def lock_card(conn: sqlite3.Connection, card_id: int) -> dict:
    """锁卡：三句话验证通过后锁定，A/B、周期、主价差、止损不可改。"""
    problems = validate_card(conn, card_id)
    if problems:
        raise ValueError("锁卡前必须通过完整性校验: " + "; ".join(problems))
    conn.execute("UPDATE cards SET status='locked' WHERE id=?", (card_id,))
    conn.commit()
    return {"card_id": card_id, "status": "locked"}


def transition(conn: sqlite3.Connection, card_id: int, status: str, note: str = "") -> dict:
    """状态迁移：candidate/locked → review/downgraded/void。"""
    if status not in STATUSES:
        raise ValueError(f"状态必须为 {STATUSES}")
    row = conn.execute("SELECT status FROM cards WHERE id=?", (card_id,)).fetchone()
    if row is None:
        raise ValueError(f"卡片 {card_id} 不存在")
    cur_status = row["status"]
    allowed = {
        "candidate": ("locked", "void"),
        "locked": ("review", "downgraded", "void"),
        "review": ("locked", "downgraded", "void"),
    }
    if status not in allowed.get(cur_status, ()):
        raise ValueError(f"状态迁移不合法：{cur_status} → {status}")
    conn.execute(
        "UPDATE cards SET status=?, review_note=? WHERE id=?",
        (status, note, card_id),
    )
    conn.commit()
    return {"card_id": card_id, "status": status}


def list_cards(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM cards WHERE status=? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cards ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def weakest_card(conn: sqlite3.Connection) -> dict | None:
    """最弱卡片：B 级及以上中等级最低、无 target 或 target 最远的。"""
    rows = conn.execute(
        """SELECT * FROM cards WHERE status IN ('candidate','locked','review')
           ORDER BY CASE level WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END DESC,
                    (target IS NULL) DESC, target DESC LIMIT 1"""
    ).fetchall()
    return dict(rows[0]) if rows else None


def evict_weakest(conn: sqlite3.Connection, reason: str = "容量淘汰") -> dict | None:
    """入卡超容时淘汰最弱一张（标记 downgraded）。"""
    w = weakest_card(conn)
    if w is None:
        return None
    conn.execute(
        "UPDATE cards SET status='downgraded', review_note=? WHERE id=?",
        (reason, w["id"]),
    )
    conn.commit()
    return w
