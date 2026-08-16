"""收盘后定时扫描：因子快照存档 + 变化检测 + P1 推送（TODO 2.5）。

2026-08-15 落地：
- 快照：每个交易日收盘后，把候选池/评级/四表写入 data/snapshots/<YYYY-MM-DD>.json
  （供任意历史截面复现，阶段 2 PIT 化的前置）；
- 变化检测：与最近一次快照对比，识别
  1) 新入池候选（尤其 core 级 = S/A 级关注）；
  2) 候选等级变化（core/track/rest 升降级）；
  3) 宏观/市场评级变化（宽松↔中性↔收紧、进攻↔中性↔防守）；
- P1 推送：变化以 [P1] 前缀推送到企业微信；无变化不推送。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from invest.config import get_settings
from invest.db import connect
from invest.notifier import Notifier

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "data" / "snapshots"

_LEVEL_RANK = {"rest": 0, "track": 1, "core": 2}


def _latest_snapshot_path() -> Path | None:
    if not SNAPSHOT_DIR.exists():
        return None
    files = sorted(SNAPSHOT_DIR.glob("*.json"))
    return files[-1] if files else None


def _load_snapshot(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def _pool_state(conn) -> dict:
    """候选池状态：{symbol: {level, in_date}}。"""
    rows = conn.execute(
        "SELECT symbol, level, in_date FROM candidate_pool WHERE out_date IS NULL"
    ).fetchall()
    return {r["symbol"]: {"level": r["level"], "in_date": r["in_date"]} for r in rows}


def _ratings_state(conn) -> dict:
    """评级状态：{kind: value}（最新一条）。"""
    rows = conn.execute(
        """SELECT kind, value FROM ratings r
           WHERE date = (SELECT MAX(date) FROM ratings r2 WHERE r2.kind = r.kind)"""
    ).fetchall()
    return {r["kind"]: r["value"] for r in rows}


def _quant_state(conn) -> dict:
    """四表状态摘要：最近 run_date 的行数（供快照完整性校验）。"""
    tables = ("quant_strength", "quant_rotation", "quant_temperature", "quant_capital")
    out = {}
    for t in tables:
        row = conn.execute(
            f"SELECT COUNT(*) c, MAX(run_date) d FROM {t}"
        ).fetchone()
        out[t] = {"rows": row["c"], "latest": row["d"]}
    return out


def take_snapshot(db_path: str) -> dict:
    """写入当日快照；返回快照内容。同日覆盖。"""
    conn = connect(db_path)
    try:
        snap = {
            "date": dt.date.today().isoformat(),
            "taken_at": dt.datetime.now().isoformat(timespec="seconds"),
            "pool": _pool_state(conn),
            "ratings": _ratings_state(conn),
            "quant": _quant_state(conn),
        }
    finally:
        conn.close()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{snap['date']}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    return snap


def detect_changes(conn, prev: dict, curr: dict) -> list[str]:
    """对比 prev/curr 快照，返回 P1 变化描述列表（空=无变化）。"""
    changes: list[str] = []

    # 1) 候选池：新入池 / 等级变化
    prev_pool = prev.get("pool", {})
    curr_pool = curr.get("pool", {})
    for sym, info in curr_pool.items():
        level = info.get("level", "rest")
        if sym not in prev_pool:
            tag = "S/A" if level == "core" else ("跟踪" if level == "track" else "关注")
            changes.append(f"新入池 {sym}（{tag}级）")
        else:
            old_level = prev_pool[sym].get("level", "rest")
            if level != old_level:
                direction = "升级" if _LEVEL_RANK.get(level, 0) > _LEVEL_RANK.get(old_level, 0) else "降级"
                changes.append(f"{sym} 等级变化：{old_level}→{level}（{direction}）")
    removed = [s for s in prev_pool if s not in curr_pool]
    for sym in removed:
        changes.append(f"{sym} 移出候选池")

    # 2) 评级变化
    prev_ratings = prev.get("ratings", {})
    curr_ratings = curr.get("ratings", {})
    for kind in ("macro", "market"):
        old_v = prev_ratings.get(kind)
        new_v = curr_ratings.get(kind)
        if old_v is not None and new_v is not None and old_v != new_v:
            changes.append(f"{kind} 评级变化：{old_v}→{new_v}")
        elif old_v is None and new_v is not None:
            changes.append(f"{kind} 评级新增：{new_v}")

    return changes


def run_scan_and_notify(db_path: str, force: bool = False) -> list[str]:
    """收盘后扫描：取快照 → 与上次对比 → P1 推送变化。

    force=True 时即使同日也重新推送（调试用）。返回变化列表。
    """
    conn = connect(db_path)
    try:
        prev = _load_snapshot(_latest_snapshot_path())
        curr_snap = take_snapshot(db_path)
        if prev.get("date") == curr_snap["date"] and not force:
            return []  # 同日已扫描过
        changes = detect_changes(conn, prev, curr_snap)
    finally:
        conn.close()
    if not changes:
        return []
    msg = "[P1] 收盘扫描变化\n" + "\n".join(f"- {c}" for c in changes)
    Notifier().send_text(msg, key="p1_scan", min_interval=600)
    return changes


def snapshot_exists(db_path: str, date: str | None = None) -> bool:
    """当日快照是否已存在（供调度器判断是否跳过）。"""
    date = date or dt.date.today().isoformat()
    return (SNAPSHOT_DIR / f"{date}.json").exists()


# ---------- 快照重建（[A]9：任意历史截面由当日快照复现） ----------

def rebuild_snapshot(date: str | None = None, snapshot_dir: Path | None = None) -> dict:
    """重建任意历史截面：返回 <= date 的最近一次快照内容（无则返回空）。

    date: YYYY-MM-DD；快照为收盘后状态，因此取「不晚于该日」的最近快照。
    snapshot_dir: 可传入其它快照目录（测试用）。
    返回快照 dict（含 date/pool/ratings/quant），并附 source 标记。
    """
    date = date or dt.date.today().isoformat()
    d = Path(snapshot_dir) if snapshot_dir else SNAPSHOT_DIR
    if not d.exists():
        return {"date": date, "source": "none", "rebuilt": False}
    files = sorted(d.glob("*.json"))
    # 文件名 YYYY-MM-DD.json
    best: Path | None = None
    for f in files:
        fdate = f.stem
        if fdate <= date:
            best = f  # 已排序，最后匹配的即最近的
    if best is None:
        return {"date": date, "source": "none", "rebuilt": False}
    snap = _load_snapshot(best)
    snap["rebuilt"] = True
    snap["source"] = best.name
    return snap


def rebuild_pool(db_path: str, date: str | None = None, snapshot_dir: Path | None = None) -> dict:
    """按历史截面重建候选池状态：pool: {symbol: {level, in_date}}。"""
    snap = rebuild_snapshot(date, snapshot_dir=snapshot_dir)
    return {"date": snap.get("date"), "pool": snap.get("pool", {}), "source": snap.get("source")}


def rebuild_ratings(db_path: str, date: str | None = None, snapshot_dir: Path | None = None) -> dict:
    """按历史截面重建评级状态：ratings: {kind: value}。"""
    snap = rebuild_snapshot(date, snapshot_dir=snapshot_dir)
    return {"date": snap.get("date"), "ratings": snap.get("ratings", {}), "source": snap.get("source")}


def rebuild_quant(db_path: str, date: str | None = None, snapshot_dir: Path | None = None) -> dict:
    """按历史截面重建四表摘要：quant: {table: {rows, latest}}。"""
    snap = rebuild_snapshot(date, snapshot_dir=snapshot_dir)
    return {"date": snap.get("date"), "quant": snap.get("quant", {}), "source": snap.get("source")}
