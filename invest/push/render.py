"""结构化报告 → 分通道渲染（2026-08-22，盘前报告 a0 用）。

struct 约定（报告 skill 输出）：
    {"sections": [
        {"type": "text", "text": "..."},                      # 纯文本节（可含 **加粗** 标记）
        {"type": "table", "title": "隔夜外围",                 # 表格节
         "columns": ["市场", "涨跌幅"], "rows": [["道指", "+0.98%"], ...]},
    ]}

- render_feishu(struct) -> card JSON 2.0：text→div/lark_md（**加粗**生效，去 *星号*），
  table→table 组件（飞书卡片 2.0，官方 JSON 结构）；
- render_plain(struct) -> str：企微/微信纯文本（表格转「标题 + 名称 值」紧凑行，去星号）。

飞书表格组件限制（官方文档）：单卡片最多 5 个表格；列≤50；每页行数 [1,10]。
"""
from __future__ import annotations

import re


def _strip_asterisk(text: str) -> str:
    """把单星号 *xx* 转成 **xx**（飞书 lark_md 粗体）；已有的 **xx** 原样保留。"""
    return re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"**\1**", text)


def _strip_asterisk_plain(text: str) -> str:
    """纯文本通道：去掉所有星号（含 ** 与 *），保留原文。"""
    return text.replace("*", "")


def _table_component(sec: dict) -> dict:
    """表格节 → 飞书卡片 table 组件（JSON 2.0）。"""
    columns = sec.get("columns") or []
    rows = sec.get("rows") or []
    cols_json = [
        {"name": f"c{i}", "display_name": str(c), "data_type": "text"}
        for i, c in enumerate(columns)
    ]
    rows_json = []
    for r in rows:
        row = {}
        for i, cell in enumerate(r):
            row[f"c{i}"] = str(cell) if cell is not None else "-"
        rows_json.append(row)
    return {
        "tag": "table",
        "page_size": min(max(len(rows_json), 1), 10),
        "header_style": {"text_align": "left", "bold": True, "text_size": "normal"},
        "columns": cols_json,
        "rows": rows_json,
    }


def _table_plain(sec: dict) -> str:
    """表格节 → 纯文本（标题 + 每行「名称 值 …」）。"""
    lines = []
    if sec.get("title"):
        lines.append(f"【{sec['title']}】")
    columns = sec.get("columns") or []
    for r in sec.get("rows") or []:
        lines.append("  " + " ".join(
            f"{columns[i]} {cell}" if i < len(columns) and columns[i] else str(cell)
            for i, cell in enumerate(r)
        ))
    return "\n".join(lines)


def render_feishu(struct: dict) -> dict:
    """结构化报告 → 飞书卡片 JSON 2.0（div/lark_md + table 组件）。"""
    elements: list[dict] = []
    for sec in struct.get("sections") or []:
        if sec.get("type") == "table":
            if sec.get("rows"):
                elements.append(_table_component(sec))
        else:
            text = _strip_asterisk(sec.get("text") or "")
            if text.strip():
                elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": text},
                })
    title = (struct.get("title") or "A股投资系统").replace("**", "")
    return {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": title}},
        "body": {"elements": elements},
    }


def render_plain(struct: dict) -> str:
    """结构化报告 → 纯文本（企微/微信通道；表格转紧凑行）。"""
    parts: list[str] = []
    for sec in struct.get("sections") or []:
        if sec.get("type") == "table":
            t = _table_plain(sec)
            if t.strip():
                parts.append(t)
        else:
            text = _strip_asterisk_plain(sec.get("text") or "")
            if text.strip():
                parts.append(text)
    return "\n".join(parts)
