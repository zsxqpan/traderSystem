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
    st.plotly_chart(fig, use_container_width=True)


def page_short():
    st.header("短线轨")
    temp = q.load_temperature(DB)
    if not temp.empty:
        c1, c2 = st.columns(2)
        c1.metric("市场温度", f"{temp.iloc[0]['score']:.0f}/100")
        c2.metric("行业宽度", f"{temp.iloc[0]['profit_effect']:.0%}")
    _bar(q.load_strength(DB), "obj", "rs", "行业相对强度 RS")
    st.subheader("资金属性 / 风格")
    st.dataframe(q.load_capital(DB), use_container_width=True)
    st.subheader("高相关行业对")
    st.dataframe(q.load_linkage(DB), use_container_width=True)


def page_mid():
    st.header("中线轨")
    _bar(q.load_weekly(DB), "obj", "rs", "周线相对强度 RS")
    st.subheader("拥挤度")
    st.dataframe(q.load_crowding(DB), use_container_width=True)
    st.subheader("宏观流动性加工")
    st.dataframe(q.load_macro(DB), use_container_width=True)


def page_viewpoints():
    st.header("观点库")
    col = st.selectbox("状态", ["active", "pending_review", "verified", "all"])
    df = q.load_viewpoints(DB, status=None if col == "all" else col)
    st.dataframe(df, use_container_width=True)
    st.subheader("准确率（按来源）")
    st.dataframe(q.load_accuracy(DB), use_container_width=True)


def page_discipline():
    st.header("执行纪律")
    st.subheader("评级")
    st.dataframe(q.load_ratings(DB), use_container_width=True)
    st.subheader("候选池")
    st.dataframe(q.load_pool(DB), use_container_width=True)
    st.subheader("活跃交易计划")
    st.dataframe(q.load_plans(DB), use_container_width=True)
    st.subheader("交易记录")
    st.dataframe(q.load_records(DB), use_container_width=True)


def page_backtest():
    st.header("回测")
    st.dataframe(q.load_backtests(DB), use_container_width=True)


def page_status():
    st.header("数据状态")
    st.dataframe(q.load_coverage(DB), use_container_width=True)
    st.subheader("最近任务")
    st.dataframe(q.load_jobs(DB), use_container_width=True)


PAGES = {
    "短线轨": page_short,
    "中线轨": page_mid,
    "观点库": page_viewpoints,
    "执行纪律": page_discipline,
    "回测": page_backtest,
    "数据状态": page_status,
}

choice = st.sidebar.radio("导航", list(PAGES.keys()))
PAGES[choice]()