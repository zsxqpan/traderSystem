"""仪表盘图表/分组（无 Streamlit，供页面与单测共用）。"""
from __future__ import annotations

import pandas as pd

QUAD_GROUPS = (
    ("quad_hunt", "主战场"),
    ("quad_chase", "追高风险"),
    ("quad_watch_cheap", "观察"),
    ("quad_avoid", "回避"),
)


def crowding_strength_figure(df: pd.DataFrame):
    """拥挤度×强度散点：有 quad → diamond，无 → circle；vline rs=0，hline crowding=0.8。"""
    import plotly.express as px

    from dashboard.theme import COLORWAY, MUTED, apply_fig

    if df is None or df.empty:
        return None
    d = df.copy()
    if "crowding_state" not in d.columns:
        d["crowding_state"] = ""
    if "quad_id" not in d.columns:
        d["quad_id"] = ""
    d["quad_id"] = d["quad_id"].fillna("").astype(str)
    d["marker_symbol"] = ["diamond" if str(x).strip() else "circle" for x in d["quad_id"]]
    fig = px.scatter(
        d,
        x="rs",
        y="crowding",
        color="trend_stage",
        hover_name="obj",
        hover_data=["crowding_state", "quad_id"],
        symbol="marker_symbol",
        symbol_map={"diamond": "diamond", "circle": "circle"},
        color_discrete_sequence=list(COLORWAY),
        labels={"rs": "RS 相对强度", "crowding": "拥挤度分位"},
    )
    fig.add_vline(x=0, line_dash="dash", line_color=MUTED)
    fig.add_hline(y=0.8, line_dash="dash", line_color=MUTED)
    fig.update_traces(marker={"size": 10, "opacity": 0.88, "line": {"width": 0.6, "color": "#0B1220"}})
    apply_fig(fig, height=420)
    return fig


def quad_path_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "signal_id" not in df.columns:
        return pd.DataFrame(columns=getattr(df, "columns", []))
    return df[df["signal_id"] == "quad_path"].copy()


def mid_quadrant_blocks(df: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """四象限入口始终返回；空组给空表。"""
    out: list[tuple[str, pd.DataFrame]] = []
    for sid, title in QUAD_GROUPS:
        if df is None or df.empty or "signal_id" not in df.columns:
            out.append((title, pd.DataFrame()))
        else:
            out.append((title, df[df["signal_id"] == sid].copy()))
    return out


def rotation_lead_caption(df: pd.DataFrame) -> str:
    if df is None or df.empty or "signal_id" not in df.columns or "subject" not in df.columns:
        return ""
    names = [str(s) for s in df.loc[df["signal_id"] == "rotation_lead", "subject"].tolist() if s]
    if not names:
        return ""
    return "领涨：" + "、".join(names)
