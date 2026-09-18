"""标的名称映射（2026-09-18）：报告/仪表盘的交易信号里，代码后面带上名称。

来源按优先级合并（后者只补前者缺失的）：
1. **本地缓存** `data/symbol_names.json`：akshare 全市场代码→名称（`stock_info_a_code_name`），
   由盘前任务每周刷新一次（TTL 7 天，失败沿用旧缓存，绝不阻断报告）；
2. **库内已有名称**：`auction_snapshots` / `limit_up_pool` / `dragon_tiger` / `daily_actions`
   按标的取最近一条（只查调用方给的代码，不做全表扫描）。

用法::

    from invest.data.names import lookup
    names = lookup(["300438", "002083"], db_path)   # {"300438": "乾照光电", ...}

设计取舍：`lookup` **不发网络请求**（只读缓存 + 本地库），避免报告渲染被网络拖慢；
刷新动作放在盘前任务里做一次。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
CACHE_FILE = ROOT / "data" / "symbol_names.json"
TTL_DAYS = 7

# 库内带 name 列的表（有日期列，取最近一条）
_DB_NAME_TABLES = ("auction_snapshots", "limit_up_pool", "dragon_tiger", "daily_actions")

_file_cache: dict[str, str] | None = None
_file_mtime: float | None = None


def _load_cache() -> dict[str, str]:
    """读 JSON 缓存（带 mtime 缓存；缺失/损坏返回空）。"""
    global _file_cache, _file_mtime
    try:
        mtime = CACHE_FILE.stat().st_mtime
    except OSError:
        return {}
    if _file_cache is not None and _file_mtime == mtime:
        return _file_cache
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        names = {str(k): str(v) for k, v in (data.get("names") or {}).items()}
    except Exception:
        logger.warning("读取名称缓存失败（%s），按空处理", CACHE_FILE, exc_info=True)
        names = {}
    _file_cache, _file_mtime = names, mtime
    return names


def cache_age_days() -> float | None:
    """缓存年龄（天）；文件不存在返回 None。"""
    try:
        mtime = dt.datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
    except OSError:
        return None
    return (dt.datetime.now() - mtime).total_seconds() / 86400.0


def refresh_cache(*, force: bool = False, ttl_days: int = TTL_DAYS) -> int:
    """刷新全市场代码→名称缓存（akshare）。未过期且非 force 时直接跳过；失败沿用旧缓存。"""
    age = cache_age_days()
    if not force and age is not None and age < ttl_days:
        return 0
    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        names = {
            str(r["code"]).zfill(6): str(r["name"]).strip()
            for _, r in df.iterrows()
            if str(r.get("code", "")).strip() and str(r.get("name", "")).strip()
        }
        if not names:
            logger.warning("名称刷新返回空，沿用旧缓存")
            return 0
        payload = {
            "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(names),
            "names": names,
        }
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        global _file_cache, _file_mtime
        _file_cache, _file_mtime = None, None
        logger.info("名称缓存已刷新：%d 个标的", len(names))
        return len(names)
    except Exception as exc:
        logger.warning("名称刷新失败（沿用旧缓存）: %s", exc)
        return 0


def lookup(symbols: list[str], db_path: str | None = None) -> dict[str, str]:
    """查名称：先命中本地缓存，缺的再查库内带名称的近期表。只返回查到的。"""
    wanted = [str(s).strip() for s in symbols if str(s).strip()]
    if not wanted:
        return {}
    out: dict[str, str] = {}
    cache = _load_cache()
    for sym in wanted:
        nm = cache.get(sym)
        if nm:
            out[sym] = nm
    missing = [s for s in wanted if s not in out]
    if missing and db_path:
        out.update(_lookup_db(db_path, missing))
    return out


def _lookup_db(db_path: str, symbols: list[str]) -> dict[str, str]:
    """从库内带 name 列的表补齐（每张表按日期取最近一条；缺表/缺列静默跳过）。"""
    from invest.db import connect

    found: dict[str, str] = {}
    try:
        conn = connect(db_path)
    except Exception:
        return {}
    try:
        for table in _DB_NAME_TABLES:
            todo = [s for s in symbols if s not in found]
            if not todo:
                break
            sql = (
                f"SELECT symbol, name, MAX(date) AS d FROM {table} "
                f"WHERE symbol IN ({','.join('?' for _ in todo)}) AND name IS NOT NULL "
                f"AND TRIM(name) != '' GROUP BY symbol"
            )
            try:
                for row in conn.execute(sql, tuple(todo)).fetchall():
                    sym, nm = str(row["symbol"]), str(row["name"]).strip()
                    if sym and nm:
                        found.setdefault(sym, nm)
            except Exception as exc:
                logger.debug("名称补齐跳过 %s: %s", table, exc)
                continue
    finally:
        conn.close()
    return found


def display(symbol: str, names: dict[str, str] | None = None) -> str:
    """`300438 乾照光电`；查不到名称时只返回代码（不编造）。"""
    nm = (names or {}).get(str(symbol).strip(), "")
    return f"{symbol} {nm}" if nm else str(symbol)


__all__ = ["CACHE_FILE", "TTL_DAYS", "cache_age_days", "display", "lookup", "refresh_cache"]
