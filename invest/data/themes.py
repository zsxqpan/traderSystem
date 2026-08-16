"""L3 主题/产业链清单加载与匹配（TODO [A]3，2026-08-15）。

数据源：`data/themes.json`（手工维护，首批 ≥10）。
提供：
- load_themes(): 读取主题清单（校验 id 唯一、至少 1 个）；
- theme_by_id / find_themes(industry/keyword): 按行业名或关键词匹配主题；
- themes_of_stock(): 经个股行业（industry_map）反查所属主题。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from invest.data.industry_map import industry_of

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "themes.json"


def load_themes(path: Path | None = None) -> list[dict]:
    """读取 L3 主题清单；文件缺失/损坏返回空表。"""
    p = path or DEFAULT_PATH
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return []
    themes = data.get("themes", []) if isinstance(data, dict) else []
    out = [t for t in themes if isinstance(t, dict) and t.get("id") and t.get("name")]
    # 去重（按 id）
    seen: set[str] = set()
    uniq: list[dict] = []
    for t in out:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        uniq.append(t)
    return uniq


def theme_by_id(theme_id: str, themes: list[dict] | None = None) -> dict | None:
    themes = themes if themes is not None else load_themes()
    for t in themes:
        if t.get("id") == theme_id:
            return t
    return None


def find_themes(
    text: str = "",
    industry: str = "",
    themes: list[dict] | None = None,
) -> list[dict]:
    """按行业名或关键词匹配主题：industry 精确命中 industries，text 命中 keywords/name。"""
    themes = themes if themes is not None else load_themes()
    text = (text or "").strip()
    out: list[dict] = []
    for t in themes:
        hit = False
        inds = [str(x) for x in t.get("industries", [])]
        if industry and industry in inds:
            hit = True
        if text and not hit:
            words = [str(x) for x in t.get("keywords", [])] + [str(t.get("name", ""))]
            if any(w and w in text for w in words):
                hit = True
        if hit:
            out.append(t)
    return out


def themes_of_stock(
    conn: sqlite3.Connection | None,
    symbol: str,
    themes: list[dict] | None = None,
) -> list[dict]:
    """标的 → 所属主题：先查行业，再按行业匹配；未命中行业时按 symbol 查 stocks。"""
    themes = themes if themes is not None else load_themes()
    industry = industry_of(conn, symbol)
    if industry:
        hits = find_themes(industry=industry, themes=themes)
        if hits:
            return hits
    return [t for t in themes if symbol in [str(x) for x in t.get("stocks", [])]]
