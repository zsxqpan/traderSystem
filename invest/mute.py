"""告警静音名单（2026-09-18）。

用途：标的已移出核心关注，但历史卡片/交易计划仍在库里 —— P0 监控与持仓警戒依旧会
按止损位推盘中提示。把这类标的写进 `data/alert_mute.json`，即可**静音盘中提示**
（不影响报告统计口径与历史留痕，随时可撤销）。

文件格式（缺省不存在 = 空名单，不影响任何逻辑）::

    {"symbols": ["300438", "002083"], "reason": "已移出核心关注", "updated_at": "2026-09-18"}

读取带 mtime 缓存：改文件即时生效（无需重启常驻服务）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
MUTE_FILE = ROOT / "data" / "alert_mute.json"

_cache: tuple[float, frozenset[str]] | None = None


def muted_symbols() -> set[str]:
    """当前静音标的集合（文件不存在/损坏 → 空集，不抛）。"""
    global _cache
    try:
        mtime = MUTE_FILE.stat().st_mtime
    except OSError:
        _cache = None
        return set()
    if _cache is not None and _cache[0] == mtime:
        return set(_cache[1])
    try:
        data = json.loads(MUTE_FILE.read_text(encoding="utf-8"))
        syms = {str(s).strip() for s in (data.get("symbols") or []) if str(s).strip()}
    except Exception:
        logger.warning("读取静音名单失败（%s），按空名单处理", MUTE_FILE, exc_info=True)
        syms = set()
    _cache = (mtime, frozenset(syms))
    return syms


def is_muted(symbol: str) -> bool:
    """该标的是否静音。"""
    return str(symbol or "").strip() in muted_symbols()


def set_muted(symbols: list[str], reason: str = "", *, merge: bool = True) -> list[str]:
    """写回静音名单（默认与既有名单合并），返回写入后的完整名单。"""
    import datetime as dt

    keep = muted_symbols() if merge else set()
    merged = sorted(keep | {str(s).strip() for s in symbols if str(s).strip()})
    payload = {
        "symbols": merged,
        "reason": reason,
        "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    MUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MUTE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    global _cache
    _cache = None
    return merged


def reset_cache() -> None:
    """清空缓存（测试用）。"""
    global _cache
    _cache = None
