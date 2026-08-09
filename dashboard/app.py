"""A股投资系统 · Streamlit 仪表盘。启动: myenv\\Scripts\\python.exe -m streamlit run dashboard/app.py"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard import queries as q

DB = str(Path(__file__).resolve().parents[1] / "data" / "invest.db")

st.set_page_config(page_title="A股投资系统", layout="wide")
st.title("A股投资系统 · 仪表盘")


def _bar(df, x, y, title):
    if df.empty:
        st.info("暂无数据")
        return
    fig = px.bar(df, x=x, y=y, title=title, text_auto=True)
    fig.update_layout(height=420)
    st.plotly_chart(fig, width="stretch")


def _health_line():
    """每页顶部的数据健康一行提示。"""
    try:
        h = q.load_data_health(DB)
        if h.empty:
            return
        parts = []
        for r in h.itertuples():
            d = str(r.max_date)[:10] if r.max_date is not None else "-"
            parts.append(f"{r.tbl}: {d}({r.status})")
        st.caption("数据健康 | " + " | ".join(parts))
    except Exception:  # noqa: BLE001
        pass


def page_overview():
    st.header("市场总览")
    h = q.load_data_health(DB)
    if not h.empty:
        st.subheader("数据健康")
        def _color(v):
            return "color: #c62828" if v == "过期" else ("color: #e65100" if v == "偏旧" else "color: #2e7d32")
        st.dataframe(h.style.map(lambda v: _color(v), subset=["status"]), width="stretch")

    st.subheader("市场温度趋势（近60日，冷<40 / 中性40-60 / 暖60-80 / 热>=80）")
    th = q.load_temperature_history(DB)
    if not th.empty:
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=th["run_date"], y=th["score"], mode="lines+markers", name="温度"))
        if th["profit_effect"].notna().any():
            fig.add_trace(go.Scatter(
                x=th["run_date"], y=th["profit_effect"] * 100, mode="lines",
                line=dict(dash="dot"), name="宽度%",
            ))
        for lo, hi, color, label in (
            (0, 40, "#dbe9fb", "冷"), (40, 60, "#fff6cc", "中性"),
            (60, 80, "#ffe3b3", "暖"), (80, 100, "#ffd6d6", "热"),
        ):
            fig.add_hrect(y0=lo, y1=hi, fillcolor=color, opacity=0.25, line_width=0,
                          annotation_text=label, annotation_position="left")
        fig.update_layout(height=380, yaxis_range=[0, 100], legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("温度历史数据不足（每日运行后自动积累）")

    st.subheader("当日板块涨跌热力图（面积=成交额，颜色=涨跌幅，红涨绿跌）")
    mv = q.load_latest_movers(DB)
    if not mv.empty:
        m2 = mv.dropna(subset=["pct"]).copy()
        fig = px.treemap(
            m2, path=[px.Constant("板块"), "industry"], values="amount", color="pct",
            color_continuous_scale="RdYlGn", color_continuous_midpoint=0,
        )
        fig.update_traces(texttemplate="%{label}", hovertemplate="%{label}<br>涨跌幅 %{color:.2%}<br>成交额 %{value:,.0f}")
        fig.update_layout(height=520, coloraxis_colorbar_title="涨跌幅")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("暂无板块行情数据")

    st.subheader("拥挤度 × 相对强度（右上=又强又拥挤，追高风险区）")
    cs = q.load_crowding_vs_strength(DB)
    if not cs.empty:
        fig = px.scatter(cs, x="rs", y="crowding", color="trend_stage", hover_name="obj",
                         labels={"rs": "RS 相对强度", "crowding": "拥挤度分位"})
        fig.add_vline(x=0, line_dash="dash", line_color="gray")
        fig.add_hline(y=0.8, line_dash="dash", line_color="gray")
        fig.update_layout(height=420, legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("暂无拥挤度/强度数据")


def page_rotation():
    _health_line()
    st.header("轮动与联动")

    st.subheader("板块轮动排名轨迹（1=最强；可多选板块）")
    rh = q.load_rotation_history(DB)
    if not rh.empty:
        latest_rank = rh[rh["run_date"] == rh["run_date"].max()].sort_values("rank")
        default = latest_rank["industry"].head(8).tolist()
        selected = st.multiselect("选择板块", sorted(rh["industry"].unique()), default=default)
        if selected:
            sub = rh[rh["industry"].isin(selected)]
            fig = px.line(sub, x="run_date", y="rank", color="industry", markers=True,
                          labels={"run_date": "日期", "rank": "排名"})
            fig.update_yaxes(autorange="reversed")  # 排名1在顶部
            fig.update_layout(height=420, legend=dict(orientation="h"))
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("请至少选择一个板块")
    else:
        st.info("暂无轮动历史（每日运行后自动积累）")

    st.subheader("行业联动网络（高相关板块，阈值可调）")
    threshold = st.slider("相关性阈值", 0.6, 0.95, 0.85, 0.05)
    edges = q.load_linkage_edges(DB, threshold=threshold, max_edges=150)
    if not edges.empty:
        from collections import Counter
        import math
        import plotly.graph_objects as go
        nodes = sorted(set(edges["a"]) | set(edges["b"]))
        deg = Counter(list(edges["a"]) + list(edges["b"]))
        pos = {name: (math.cos(2 * math.pi * i / len(nodes)), math.sin(2 * math.pi * i / len(nodes)))
               for i, name in enumerate(nodes)}
        ex, ey = [], []
        for r in edges.itertuples():
            x0, y0 = pos[r.a]
            x1, y1 = pos[r.b]
            ex += [x0, x1, None]
            ey += [y0, y1, None]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=ex, y=ey, mode="lines", line=dict(color="#b0b0b0", width=1),
                                 hoverinfo="none"))
        fig.add_trace(go.Scatter(
            x=[pos[n][0] for n in nodes], y=[pos[n][1] for n in nodes],
            mode="markers+text", text=nodes, textposition="middle center",
            textfont=dict(size=11),
            marker=dict(size=[10 + 5 * deg[n] for n in nodes], color="#1f77b4",
                        line=dict(color="white", width=1)),
            hovertext=[f"{n}<br>连接 {deg[n]} 个板块" for n in nodes], hoverinfo="text",
        ))
        fig.update_layout(height=560, showlegend=False, xaxis=dict(visible=False),
                          yaxis=dict(visible=False), margin=dict(l=10, r=10, t=10, b=10))
        fig.update_yaxes(scaleanchor="x", scaleratio=1)
        st.plotly_chart(fig, width="stretch")
        st.caption(f"显示 {len(nodes)} 个板块 / {len(edges)} 条高相关边（按相关性取前150条）")
    else:
        st.info(f"相关性 ≥ {threshold:.0%} 的板块对暂无")

    st.subheader("行业风格轮动时间线（各类风格占比）")
    sh = q.load_style_history(DB)
    if not sh.empty:
        pivot = sh.pivot_table(index="run_date", columns="style", values="n", aggfunc="sum").fillna(0)
        share = pivot.div(pivot.sum(axis=1), axis=0) * 100
        fig = px.area(share, labels={"value": "占比%", "run_date": "日期", "style": "风格"})
        fig.update_layout(height=380, legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("暂无风格历史（每日运行后自动积累）")


def page_short():
    _health_line()
    st.header("短线轨")
    temp = q.load_temperature(DB)
    if not temp.empty:
        c1, c2 = st.columns(2)
        c1.metric("市场温度", f"{temp.iloc[0]['score']:.0f}/100")
        c2.metric("行业宽度", f"{temp.iloc[0]['profit_effect']:.0%}")
    _bar(q.load_strength(DB), "obj", "rs", "行业相对强度 RS")
    st.subheader("资金属性 / 风格")
    st.dataframe(q.load_capital(DB), width="stretch")
    st.subheader("高相关行业对")
    st.dataframe(q.load_linkage(DB), width="stretch")


def page_mid():
    _health_line()
    st.header("中线轨")
    _bar(q.load_weekly(DB), "obj", "rs", "周线相对强度 RS")
    st.subheader("拥挤度")
    st.dataframe(q.load_crowding(DB), width="stretch")
    st.subheader("宏观流动性加工")
    st.dataframe(q.load_macro(DB), width="stretch")


def page_viewpoints():
    _health_line()
    st.header("观点库")
    col = st.selectbox("状态", ["active", "pending_review", "verified", "all"])
    df = q.load_viewpoints(DB, status=None if col == "all" else col)
    st.dataframe(df, width="stretch")
    st.subheader("准确率（按来源）")
    st.dataframe(q.load_accuracy(DB), width="stretch")


def page_discipline():
    _health_line()
    st.header("执行纪律")
    st.subheader("评级 → 建议仓位")
    pl = q.load_position_limit(DB)
    c1, c2, c3 = st.columns(3)
    c1.metric("宏观评级", pl.get("macro") or "未评")
    c2.metric("市场评级", pl.get("market") or "未评")
    c3.metric("建议总仓位上限", f"{pl.get('position_limit', 0.5):.0%}")
    st.subheader("评级明细")
    st.dataframe(q.load_ratings(DB), width="stretch")
    st.subheader("候选池")
    st.dataframe(q.load_pool(DB), width="stretch")
    st.subheader("活跃交易计划")
    st.dataframe(q.load_plans(DB), width="stretch")
    st.subheader("交易记录")
    st.dataframe(q.load_records(DB), width="stretch")


def page_backtest():
    _health_line()
    st.header("回测")
    st.dataframe(q.load_backtests(DB), width="stretch")


def page_status():
    _health_line()
    st.header("数据状态")
    st.dataframe(q.load_coverage(DB), width="stretch")
    st.subheader("最近任务")
    st.dataframe(q.load_jobs(DB), width="stretch")


PAGES = {
    "市场总览": page_overview,
    "轮动与联动": page_rotation,
    "短线轨": page_short,
    "中线轨": page_mid,
    "观点库": page_viewpoints,
    "执行纪律": page_discipline,
    "回测": page_backtest,
    "数据状态": page_status,
}

choice = st.sidebar.radio("导航", list(PAGES.keys()))
PAGES[choice]()