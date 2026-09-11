"""仪表盘设计系统：配色、CSS、Plotly 主题、共用展示组件。

深色投研台气质（非玩具风）：信息密度适中、层级清楚、A 股红涨绿跌。
"""
from __future__ import annotations

from typing import Any

# ---------- 色板（A 股语义） ----------
BG = "#0B1220"
SURFACE = "#121A2B"
SURFACE_2 = "#182236"
BORDER = "#243044"
TEXT = "#E8EEF7"
MUTED = "#8B97AD"
GOLD = "#C9A227"
UP = "#E03131"       # 涨 / 行动
DOWN = "#2F9E44"     # 跌
AMBER = "#F08C00"    # 观察 / 偏旧
BLUE = "#4C8DFF"     # 提示
OK = "#2F9E44"

# Plotly 离散色（板块线/风格面积，避开纯霓虹）
COLORWAY = [
    "#4C8DFF", "#E03131", "#C9A227", "#2F9E44", "#F08C00",
    "#7C6AF2", "#2BB8A8", "#D96BA0", "#8B97AD", "#E8D48B",
]

# A 股涨跌连续色：绿(跌) → 浅金 → 红(涨)
A_SHARE_SCALE = (
    (0.0, DOWN),
    (0.5, "#E8D48B"),
    (1.0, UP),
)

# 温度带（深色底上的半透明色块）
TEMP_BANDS = (
    (0, 40, "rgba(27,58,92,0.55)", "冷"),
    (40, 60, "rgba(58,53,24,0.50)", "中性"),
    (60, 80, "rgba(74,46,18,0.50)", "暖"),
    (80, 100, "rgba(74,28,28,0.50)", "热"),
)

_SEV_STYLE = {
    "action": f"color:{UP};font-weight:600",
    "行动": f"color:{UP};font-weight:600",
    "watch": f"color:{AMBER};font-weight:600",
    "观察": f"color:{AMBER};font-weight:600",
    "info": f"color:{BLUE}",
    "提示": f"color:{BLUE}",
}
_STATUS_STYLE = {
    "正常": f"color:{OK};font-weight:600",
    "偏旧": f"color:{AMBER};font-weight:600",
    "过期": f"color:{UP};font-weight:600",
    "ok": f"color:{OK};font-weight:600",
    "error": f"color:{UP};font-weight:600",
    "pending": f"color:{AMBER}",
    "triggered": f"color:{UP};font-weight:600",
    "done": f"color:{OK}",
    "skipped": f"color:{MUTED}",
}
_VERB_STYLE = {
    "买": f"color:{UP};font-weight:600",
    "加": f"color:{UP};font-weight:600",
    "卖": f"color:{DOWN};font-weight:600",
    "减": f"color:{DOWN};font-weight:600",
    "持": f"color:{MUTED}",
    "等": f"color:{AMBER}",
    "看": f"color:{BLUE}",
}

PAGE_SUBTITLES = {
    "市场总览": "温度 · 板块热力 · 拥挤度与强度",
    "交易信号": "只读 trade_signals · 规则扫描，不自动下单",
    "轮动与联动": "排名轨迹 · 高相关网络 · 风格占比",
    "短线轨": "当日温度、相对强度与短线信号",
    "中线轨": "周线强度、拥挤度与四象限",
    "中期比价": "事实卡并排 · 综合买卖由人判断",
    "观点库": "观点与来源准确率",
    "大V画像库": "选人提问 · 默认有据，一期只采雪球",
    "执行纪律": "动作清单 / 观察名单 / 评级仓位 · 不自动入 core",
    "回测": "历史回测批次",
    "数据状态": "覆盖区间 · 最近任务 · 报告回看",
}

CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap');

:root {{
  --ts-bg: {BG};
  --ts-surface: {SURFACE};
  --ts-surface-2: {SURFACE_2};
  --ts-border: {BORDER};
  --ts-text: {TEXT};
  --ts-muted: {MUTED};
  --ts-gold: {GOLD};
  --ts-up: {UP};
  --ts-down: {DOWN};
  --ts-amber: {AMBER};
  --ts-blue: {BLUE};
}}

html, body, [data-testid="stAppViewContainer"] {{
  background: var(--ts-bg) !important;
  color: var(--ts-text);
}}

.stApp {{
  background:
    radial-gradient(1200px 500px at 8% -10%, rgba(201,162,39,0.07), transparent 50%),
    radial-gradient(900px 420px at 100% 0%, rgba(76,141,255,0.06), transparent 46%),
    var(--ts-bg) !important;
}}

/* 顶栏绝对定位会盖住标题；整行必须透明，只留展开按钮可点。 */
[data-testid="stHeader"],
.stAppHeader {{
  background: transparent !important;
  backdrop-filter: none !important;
  box-shadow: none !important;
  pointer-events: none;
}}
[data-testid="stToolbar"],
.stAppToolbar {{
  background: transparent !important;
  pointer-events: none;
}}

.block-container {{
  padding-top: 1.15rem !important;
  padding-bottom: 2.6rem !important;
  max-width: 1440px !important;
}}

h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {{
  font-family: "IBM Plex Sans", "PingFang SC", "Microsoft YaHei", sans-serif !important;
  letter-spacing: 0.01em;
  color: var(--ts-text) !important;
}}

p, span, label, .stMarkdown, .stCaption, .stText {{
  font-family: "IBM Plex Sans", "PingFang SC", "Microsoft YaHei", sans-serif;
}}

[data-testid="stMetricValue"] {{
  font-variant-numeric: tabular-nums;
  font-weight: 600 !important;
}}
[data-testid="stMetric"] {{
  background: var(--ts-surface);
  border: 1px solid var(--ts-border);
  border-radius: 10px;
  padding: 0.65rem 0.85rem 0.55rem;
}}
[data-testid="stMetricLabel"] {{
  color: var(--ts-muted) !important;
}}

[data-testid="stSidebar"] {{
  background: {SURFACE} !important;
  border-right: 1px solid var(--ts-border);
}}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{
  color: var(--ts-muted);
}}

section[data-testid="stSidebar"] .stButton button {{
  justify-content: flex-start;
  text-align: left;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--ts-text);
  font-weight: 500;
  padding: 0.38rem 0.7rem;
}}
section[data-testid="stSidebar"] .stButton button[kind="primary"] {{
  background: rgba(201,162,39,0.14);
  border-color: rgba(201,162,39,0.45);
  color: var(--ts-gold);
}}
section[data-testid="stSidebar"] .stButton button[kind="secondary"]:hover {{
  background: var(--ts-surface-2);
  border-color: var(--ts-border);
}}

.stButton button {{
  border-radius: 8px;
  font-weight: 500;
}}

.stSelectbox, .stMultiSelect, .stTextInput, .stTextArea {{
  border-radius: 8px;
}}

[data-testid="stDataFrame"] {{
  border: 1px solid var(--ts-border);
  border-radius: 10px;
  overflow: hidden;
}}

div[data-testid="stExpander"] {{
  background: var(--ts-surface);
  border: 1px solid var(--ts-border);
  border-radius: 10px;
}}

.stChatMessage {{
  background: var(--ts-surface) !important;
  border: 1px solid var(--ts-border);
  border-radius: 10px;
}}

.ts-brand {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0.15rem 0.15rem 0.85rem;
  border-bottom: 1px solid var(--ts-border);
  margin-bottom: 0.85rem;
}}
.ts-brand-mark {{
  width: 28px; height: 28px;
  border-radius: 7px;
  background: linear-gradient(160deg, #C9A227 0%, #8A6E12 100%);
  color: #0B1220;
  font-weight: 700;
  font-size: 14px;
  display: flex; align-items: center; justify-content: center;
}}
.ts-brand-name {{
  font-size: 0.98rem;
  font-weight: 600;
  color: var(--ts-text);
  line-height: 1.2;
}}
.ts-brand-sub {{
  font-size: 0.72rem;
  color: var(--ts-muted);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.ts-nav-group {{
  font-size: 0.7rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ts-muted);
  margin: 0.7rem 0.15rem 0.28rem;
}}
.ts-hero {{
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 1rem;
  margin: 0 0 1.05rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--ts-border);
}}
.ts-hero .ts-title {{
  margin: 0;
  font-size: 1.55rem;
  font-weight: 600;
  color: var(--ts-text);
}}
.ts-hero p {{
  margin: 0.28rem 0 0;
  color: var(--ts-muted);
  font-size: 0.88rem;
}}
.ts-pills {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 0.45rem;
  margin: 0 0 0.95rem;
}}
.ts-pill {{
  font-size: 0.75rem;
  padding: 0.18rem 0.55rem;
  border-radius: 999px;
  border: 1px solid var(--ts-border);
  background: var(--ts-surface);
  color: var(--ts-muted);
  font-variant-numeric: tabular-nums;
}}
.ts-pill.ok {{ color: {OK}; border-color: rgba(47,158,68,0.35); }}
.ts-pill.warn {{ color: {AMBER}; border-color: rgba(240,140,0,0.4); }}
.ts-pill.bad {{ color: {UP}; border-color: rgba(224,49,49,0.4); }}
.ts-empty {{
  border: 1px dashed var(--ts-border);
  border-radius: 10px;
  padding: 1.15rem 1rem;
  color: var(--ts-muted);
  background: rgba(18,26,43,0.55);
  text-align: center;
  font-size: 0.9rem;
}}
.ts-section {{
  margin: 1.15rem 0 0.45rem;
}}
.ts-section .ts-h {{
  margin: 0;
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--ts-text);
}}
.ts-section .sub {{
  margin: 0.2rem 0 0;
  color: var(--ts-muted);
  font-size: 0.8rem;
}}
.ts-toolbar {{
  background: var(--ts-surface);
  border: 1px solid var(--ts-border);
  border-radius: 10px;
  padding: 0.55rem 0.75rem 0.15rem;
  margin-bottom: 0.85rem;
}}
.ts-card {{
  background: var(--ts-surface);
  border: 1px solid var(--ts-border);
  border-radius: 10px;
  padding: 0.85rem 0.95rem;
  margin-bottom: 0.75rem;
}}
.ts-report {{
  background: var(--ts-surface);
  border: 1px solid var(--ts-border);
  border-left: 3px solid var(--ts-gold);
  border-radius: 10px;
  padding: 0.9rem 1.05rem;
  font-size: 0.92rem;
  line-height: 1.65;
  white-space: pre-wrap;
  font-variant-numeric: tabular-nums;
}}
footer, #MainMenu {{
  visibility: hidden;
  height: 0;
}}
/* 折叠后展开按钮在 stToolbar 内：可见可点，但不铺整行色带。 */
[data-testid="stExpandSidebarButton"] {{
  visibility: visible !important;
  opacity: 1 !important;
  pointer-events: auto !important;
  color: {TEXT} !important;
  background: {SURFACE} !important;
  border: 1px solid {BORDER} !important;
  border-radius: 8px !important;
}}
[data-testid="stExpandSidebarButton"] svg {{
  fill: {TEXT} !important;
}}
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapseButton"] svg {{
  color: {TEXT} !important;
  fill: {TEXT} !important;
}}
[data-testid="stHeaderActionElements"] {{
  display: none !important;
}}
"""


def inject() -> None:
    """注入全局 CSS。须在 set_page_config 之后调用。"""
    import streamlit as st

    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def apply_fig(fig: Any, height: int | None = None) -> Any:
    """给 Plotly 图套上投研台深色主题。"""
    if fig is None:
        return None
    kw: dict[str, Any] = {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(18,26,43,0.55)",
        "font": {"color": TEXT, "family": "IBM Plex Sans, PingFang SC, Microsoft YaHei, sans-serif"},
        "colorway": list(COLORWAY),
        "legend": {"orientation": "h", "bgcolor": "rgba(0,0,0,0)", "font": {"size": 11}},
        "margin": {"l": 48, "r": 16, "t": 48, "b": 40},
        "hoverlabel": {"bgcolor": SURFACE_2, "font": {"color": TEXT}},
    }
    if height is not None:
        kw["height"] = height
    fig.update_layout(**kw)
    fig.update_xaxes(gridcolor="rgba(36,48,68,0.65)", zerolinecolor="rgba(139,151,173,0.35)")
    fig.update_yaxes(gridcolor="rgba(36,48,68,0.65)", zerolinecolor="rgba(139,151,173,0.35)")
    return fig


def empty(msg: str) -> None:
    import streamlit as st

    st.markdown(f'<div class="ts-empty">{_esc(msg)}</div>', unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "") -> None:
    import streamlit as st

    sub = subtitle or PAGE_SUBTITLES.get(title, "")
    st.markdown(
        f'<div class="ts-hero"><div><div class="ts-title">{_esc(title)}</div>'
        f'<p>{_esc(sub)}</p></div></div>',
        unsafe_allow_html=True,
    )


def section(title: str, subtitle: str = "") -> None:
    import streamlit as st

    extra = f'<p class="sub">{_esc(subtitle)}</p>' if subtitle else ""
    st.markdown(
        f'<div class="ts-section"><div class="ts-h">{_esc(title)}</div>{extra}</div>',
        unsafe_allow_html=True,
    )


def brand_html() -> str:
    return (
        '<div class="ts-brand">'
        '<div class="ts-brand-mark">投</div>'
        '<div><div class="ts-brand-name">A股投资系统</div>'
        '<div class="ts-brand-sub">Research Desk</div></div></div>'
    )


def nav_group_html(label: str) -> str:
    return f'<div class="ts-nav-group">{_esc(label)}</div>'


def health_pills(df) -> None:
    """数据健康：胶囊条。缺表/空表则静默。"""
    import streamlit as st

    if df is None or getattr(df, "empty", True):
        return
    pills = []
    for r in df.itertuples():
        status = str(getattr(r, "status", "") or "")
        raw_d = getattr(r, "max_date", None)
        d = "-"
        if raw_d is not None and str(raw_d) not in ("NaT", "NaN", "None", ""):
            d = str(raw_d)[:10]
        tbl = str(getattr(r, "tbl", ""))
        cls = "ok" if status == "正常" else ("warn" if status == "偏旧" else "bad")
        pills.append(
            f'<span class="ts-pill {cls}">{_esc(tbl)} · {d} · {_esc(status)}</span>'
        )
    st.markdown('<div class="ts-pills">' + "".join(pills) + "</div>", unsafe_allow_html=True)


def report_block(text: str) -> None:
    import streamlit as st

    st.markdown(f'<div class="ts-report">{_esc(text)}</div>', unsafe_allow_html=True)


def style_df(df, kind: str | None = None):
    """按列语义上色。缺 jinja2 / 空表时原样返回，页面不炸。"""
    if df is None or getattr(df, "empty", True):
        return df
    try:
        sty = df.style
    except Exception:
        return df
    try:
        cols = set(df.columns)
        if "status" in cols and (kind in ("health", "jobs", "actions") or "lag_days" in cols or "job" in cols):
            sty = sty.map(_status_css, subset=["status"])
        if "severity" in cols:
            sty = sty.map(_sev_css, subset=["severity"])
        if "verb" in cols:
            sty = sty.map(_verb_css, subset=["verb"])
        if "pct" in cols:
            sty = sty.map(_pct_css, subset=["pct"])
        return sty
    except Exception:
        return df


def _sev_css(v) -> str:
    return _SEV_STYLE.get(str(v).strip(), f"color:{MUTED}")


def _status_css(v) -> str:
    return _STATUS_STYLE.get(str(v).strip(), f"color:{MUTED}")


def _verb_css(v) -> str:
    return _VERB_STYLE.get(str(v).strip(), f"color:{TEXT}")


def _pct_css(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x > 0:
        return f"color:{UP};font-variant-numeric:tabular-nums"
    if x < 0:
        return f"color:{DOWN};font-variant-numeric:tabular-nums"
    return f"color:{MUTED}"


def _esc(s: str) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
