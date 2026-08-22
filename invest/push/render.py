"""结构化报告 → 分通道渲染（2026-08-22，盘前报告 a0 / 盘中报告 b1 用）。

struct 约定（报告 skill 输出）：
    {"sections": [
        {"type": "text", "text": "..."},                      # 纯文本节（可含 **加粗** 标记）
        {"type": "table", "title": "隔夜外围",                 # 表格节
         "columns": ["市场", "涨跌幅"], "rows": [["道指", "+0.98%"], ...]},
        {"type": "chart", "chart": "index_bars"|"temp_curve",  # 图表节（2026-08-22）
         "title": "...", "data": [{"name": ..., "value": ...}] | [{"date": ..., "score": ...}]},
    ]}

- render_feishu(struct, upload_fn=None) -> card JSON 2.0：
  text→div/lark_md（**加粗**生效，去 *星号*）；table→table 组件；
  chart→matplotlib 生成 PNG → 飞书上传（upload_fn 缺省用 feishu_push.upload_image）
  → image 组件（上传失败降级为文本行，不阻断）；
- render_plain(struct) -> str：企微/微信纯文本（表格转「标题 + 名称 值」紧凑行，
  图表转数据文本行，去星号）。

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


# ---------- 图表（2026-08-22：matplotlib 生成 PNG → 飞书 image 组件） ----------

def _chart_png(sec: dict) -> bytes | None:
    """图表节 → PNG bytes（Agg 无头后端；A 股红涨绿跌）。失败返回 None。"""
    try:
        import io

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager

        # Windows 微软雅黑（无则回退默认字体，中文可能方块——尽力而为）
        try:
            font_manager.fontManager.addfont(r"C:\Windows\Fonts\msyh.ttc")
            plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
        except Exception:
            pass
        plt.rcParams["axes.unicode_minus"] = False

        kind = sec.get("chart")
        title = (sec.get("title") or "图表").replace("**", "")
        data = sec.get("data") or []
        fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=110)
        if kind == "index_bars":
            names = [str(d.get("name", "")) for d in data]
            vals = [float(d.get("value", 0)) for d in data]
            colors = ["#e03131" if v >= 0 else "#2f9e44" for v in vals]
            ax.barh(names, vals, color=colors)
            ax.axvline(0, color="#666", linewidth=0.8)
            for i, v in enumerate(vals):
                ax.text(v + (0.02 if v >= 0 else -0.02), i, f"{v:+.2f}%",
                        va="center", ha="left" if v >= 0 else "right", fontsize=9)
            ax.set_title(title, fontsize=12)
            ax.set_xlim(min(vals) - 0.6 if vals else -1, max(vals) + 0.6 if vals else 1)
        elif kind == "temp_curve":
            dates = [str(d.get("date", ""))[5:] for d in data]
            scores = [float(d.get("score", 0)) for d in data]
            ax.plot(dates, scores, marker="o", markersize=4, color="#1971c2", linewidth=1.6)
            if scores:
                ax.scatter([dates[-1]], [scores[-1]], s=60, color="#e03131", zorder=5)
            ax.set_title(title, fontsize=12)
            ax.set_ylim(0, 100)
            ax.grid(True, alpha=0.3)
            plt.xticks(rotation=45, fontsize=8)
        else:
            plt.close(fig)
            return None
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


def _chart_plain(sec: dict) -> str:
    """图表节 → 纯文本（数据行）。"""
    lines = [f"📊 {sec.get('title') or '图表'}"]
    for d in sec.get("data") or []:
        if sec.get("chart") == "temp_curve":
            lines.append(f"  {d.get('date', '')} {d.get('score', '')}")
        else:
            v = d.get("value")
            lines.append(f"  {d.get('name', '')} {v:+.2f}%" if v is not None else f"  {d.get('name', '')}")
    return "\n".join(lines)


def render_feishu(struct: dict, upload_fn=None) -> dict:
    """结构化报告 → 飞书卡片 JSON 2.0（div/lark_md + table + image 组件）。

    upload_fn: 图表上传函数 (bytes) -> image_key；缺省用 feishu_push.upload_image。
    """
    if upload_fn is None:
        try:
            from invest.push.feishu_push import upload_image as upload_fn
        except Exception:
            upload_fn = None
    elements: list[dict] = []
    for sec in struct.get("sections") or []:
        if sec.get("type") == "table":
            if sec.get("rows"):
                elements.append(_table_component(sec))
        elif sec.get("type") == "chart":
            img_key = None
            if upload_fn is not None:
                png = _chart_png(sec)
                if png:
                    img_key = upload_fn(png)
            if img_key:
                elements.append({
                    "tag": "img",
                    "img_key": img_key,
                    "alt": {"tag": "plain_text", "content": (sec.get("title") or "图表")},
                })
            else:
                t = _chart_plain(sec)
                if t.strip():
                    elements.append({
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": t},
                    })
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
    """结构化报告 → 纯文本（企微/微信通道；表格转紧凑行、图表转数据行）。"""
    parts: list[str] = []
    for sec in struct.get("sections") or []:
        if sec.get("type") == "table":
            t = _table_plain(sec)
            if t.strip():
                parts.append(t)
        elif sec.get("type") == "chart":
            t = _chart_plain(sec)
            if t.strip():
                parts.append(t)
        else:
            text = _strip_asterisk_plain(sec.get("text") or "")
            if text.strip():
                parts.append(text)
    return "\n".join(parts)
