"""仪表盘主题 / 导航分组 / 报告排版扫读（不连真实网络）。"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_nav_groups_cover_pages_order():
    from dashboard.nav import NAV_GROUPS, PAGES_ORDER

    flat = tuple(name for _, names in NAV_GROUPS for name in names)
    assert set(flat) == set(PAGES_ORDER)
    assert len(flat) == len(PAGES_ORDER)


def test_css_does_not_hide_sidebar_expand_control():
    """折叠后展开钮在 stToolbar 内；禁止整栏 visibility:hidden / height:0。"""
    from dashboard import theme

    css = theme.CSS
    assert "stExpandSidebarButton" in css
    assert "visibility: visible !important" in css
    assert "pointer-events: auto !important" in css
    hide = None
    for chunk in css.split("}"):
        if '[data-testid="stToolbar"]' in chunk and "visibility: hidden" in chunk:
            hide = chunk
            break
    assert hide is None, hide


def test_css_header_does_not_paint_opaque_bar():
    """顶栏不得用半透明底+模糊盖住标题。"""
    from dashboard import theme

    css = theme.CSS
    assert "rgba(11,18,32,0.72)" not in css
    assert "backdrop-filter: none" in css
    assert '[data-testid="stHeader"]' in css
    assert "background: transparent !important" in css


def test_theme_tokens_are_a_share_desk():
    from dashboard import theme

    assert theme.UP == "#E03131"
    assert theme.DOWN == "#2F9E44"
    assert theme.BG.startswith("#")
    assert "--ts-up" in theme.CSS and "--ts-down" in theme.CSS
    assert "ts-hero" in theme.CSS and "ts-empty" in theme.CSS
    assert theme.A_SHARE_SCALE[0][1] == theme.DOWN
    assert theme.A_SHARE_SCALE[-1][1] == theme.UP


def test_apply_fig_keeps_scatter_shapes():
    from dashboard.charts import crowding_strength_figure
    from dashboard.theme import apply_fig

    df = pd.DataFrame([
        {"obj": "半导体", "rs": 0.25, "crowding": 0.3, "trend_stage": "加速",
         "crowding_state": "正常", "quad_id": "quad_hunt"},
        {"obj": "银行", "rs": -0.05, "crowding": 0.85, "trend_stage": "震荡",
         "crowding_state": "高拥挤", "quad_id": ""},
    ])
    fig = crowding_strength_figure(df)
    assert fig is not None
    apply_fig(fig, height=360)
    xs = [getattr(s, "x0", None) for s in (fig.layout.shapes or [])]
    ys = [getattr(s, "y0", None) for s in (fig.layout.shapes or [])]
    assert 0 in xs or 0.0 in xs
    assert 0.8 in ys or any(y is not None and abs(float(y) - 0.8) < 1e-9 for y in ys if y is not None)


def test_style_df_colors_severity_and_verb():
    from dashboard import theme

    assert "e03131" in theme._sev_css("action").lower()
    assert "e03131" in theme._verb_css("买").lower()
    assert "2f9e44" in theme._verb_css("卖").lower()
    assert theme.UP.lower().lstrip("#") in theme._pct_css(0.03).lower()
    assert theme.DOWN.lower().lstrip("#") in theme._pct_css(-0.02).lower()
    df = pd.DataFrame([{"severity": "action", "verb": "买", "status": "pending"}])
    out = theme.style_df(df, "signals")
    assert out is not None


def test_format_signals_uses_cn_severity():
    from invest.signals.format import format_signals, signal_section
    from invest.signals.types import Signal

    s = Signal(
        id="high_vol", name="高位放量", session="intraday", severity="action",
        subject_type="stock", subject="600519", hint="量比放大",
    )
    text = format_signals([s])
    assert text.startswith("【交易信号】")
    assert "[行动]" in text and "600519" in text
    blk = signal_section([s])
    assert blk and blk["text"].startswith("**【交易信号】**")


def test_render_plain_gaps_and_table_pairs():
    from invest.push.render import render_feishu, render_plain

    struct = {
        "title": "测试",
        "sections": [
            {"type": "text", "text": "**【标题】**\n*强调* 内容"},
            {"type": "table", "title": "隔夜外围", "columns": ["市场", "涨跌幅"],
             "rows": [["道指", "+0.98%"]]},
        ],
    }
    plain = render_plain(struct)
    assert "*" not in plain
    assert "【标题】" in plain and "市场 道指" in plain and "+0.98%" in plain
    assert "\n\n" in plain
    card = render_feishu(struct)
    tags = [e.get("tag") for e in card["body"]["elements"]]
    assert "hr" in tags and "table" in tags
    assert card["header"]["title"]["content"] == "测试"
