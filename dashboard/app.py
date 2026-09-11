"""A股投资系统 · Streamlit 仪表盘。启动: myenv\\Scripts\\python.exe -m streamlit run dashboard/app.py"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard import queries as q
from dashboard.charts import (
    crowding_strength_figure,
    mid_quadrant_blocks,
    quad_path_frame,
    rotation_lead_caption,
)
from dashboard.nav import NAV_GROUPS, PAGES_ORDER
from dashboard.theme import (
    A_SHARE_SCALE,
    COLORWAY,
    MUTED,
    TEMP_BANDS,
    apply_fig,
    brand_html,
    empty,
    health_pills,
    inject,
    nav_group_html,
    page_header,
    report_block,
    section,
    style_df,
)

DB = str(Path(__file__).resolve().parents[1] / "data" / "invest.db")

st.set_page_config(
    page_title="A股投资系统",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject()


def _bar(df, x, y, title):
    if df.empty:
        empty("暂无数据")
        return
    fig = px.bar(df, x=x, y=y, title=title, text_auto=True, color_discrete_sequence=list(COLORWAY))
    apply_fig(fig, height=420)
    st.plotly_chart(fig, width="stretch")


def _safe_df(fn, *args, **kwargs):
    """缺表/缺列时给空表，页面不炸。"""
    try:
        out = fn(*args, **kwargs)
        return out if out is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _health_line():
    """每页顶部的数据健康胶囊。"""
    try:
        h = q.load_data_health(DB)
        health_pills(h)
    except Exception:
        pass


def _show(df, kind: str | None = None):
    if df is None or df.empty:
        empty("暂无数据")
        return
    st.dataframe(style_df(df, kind), width="stretch")


def page_overview():
    page_header("市场总览")
    h = q.load_data_health(DB)
    health_pills(h)
    if not h.empty:
        n_ok = int((h["status"] == "正常").sum())
        n_bad = int((h["status"] == "过期").sum())
        temp = _safe_df(q.load_temperature, DB)
        c1, c2, c3, c4 = st.columns(4)
        if not temp.empty:
            c1.metric("市场温度", f"{temp.iloc[0]['score']:.0f}/100")
            pe = temp.iloc[0].get("profit_effect")
            c2.metric("行业宽度", f"{float(pe):.0%}" if pe is not None and pd.notna(pe) else "—")
        else:
            c1.metric("市场温度", "—")
            c2.metric("行业宽度", "—")
        c3.metric("数据正常", f"{n_ok}/{len(h)}")
        c4.metric("过期表", str(n_bad))

    section("市场温度趋势", "近60日 · 冷<40 / 中性40-60 / 暖60-80 / 热>=80")
    th = q.load_temperature_history(DB)
    if not th.empty:
        import plotly.graph_objects as go
        fig = go.Figure()
        th = th.copy()
        th["run_date"] = th["run_date"].astype(str).str[:10]
        fig.add_trace(go.Scatter(
            x=th["run_date"], y=th["score"], mode="lines+markers", name="温度",
            line={"color": "#C9A227", "width": 2},
            marker={"size": 5, "color": "#C9A227"},
        ))
        if th["profit_effect"].notna().any():
            fig.add_trace(go.Scatter(
                x=th["run_date"], y=th["profit_effect"] * 100, mode="lines",
                line={"dash": "dot", "color": MUTED, "width": 1.4}, name="宽度%",
            ))
        for lo, hi, color, label in TEMP_BANDS:
            fig.add_hrect(
                y0=lo, y1=hi, fillcolor=color, line_width=0,
                annotation_text=label, annotation_position="left",
                annotation_font={"color": MUTED, "size": 11},
            )
        apply_fig(fig, height=380)
        fig.update_layout(yaxis_range=[0, 100])
        st.plotly_chart(fig, width="stretch")
    else:
        empty("温度历史数据不足（每日运行后自动积累）")

    section("当日板块涨跌热力图", "面积=成交额，颜色=涨跌幅（红涨绿跌）")
    mv = q.load_latest_movers(DB)
    if not mv.empty:
        m2 = mv.dropna(subset=["pct"]).copy()
        fig = px.treemap(
            m2, path=[px.Constant("板块"), "industry"], values="amount", color="pct",
            color_continuous_scale=list(A_SHARE_SCALE), color_continuous_midpoint=0,
        )
        fig.update_traces(
            texttemplate="%{label}",
            hovertemplate="%{label}<br>涨跌幅 %{color:.2%}<br>成交额 %{value:,.0f}",
        )
        apply_fig(fig, height=520)
        fig.update_layout(coloraxis_colorbar_title="涨跌幅")
        st.plotly_chart(fig, width="stretch")
    else:
        empty("暂无板块行情数据")

    section("拥挤度 × 相对强度", "右上=又强又拥挤，追高风险区")
    cs = _safe_df(q.load_crowding_vs_strength, DB)
    if not cs.empty:
        fig = crowding_strength_figure(cs)
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        paths = quad_path_frame(_safe_df(q.load_signals, DB, horizon="mid", days=5))
        if not paths.empty:
            st.caption("近5日拥挤路径")
            _show(paths, "signals")
    else:
        empty("暂无拥挤度/强度数据")


def page_rotation():
    _health_line()
    page_header("轮动与联动")
    lead_cap = rotation_lead_caption(_safe_df(q.load_signals, DB, horizon="mid", session="daily"))
    if lead_cap:
        st.caption(lead_cap)

    section("板块轮动排名轨迹", "1=最强；可多选板块")
    rh = q.load_rotation_history(DB)
    if not rh.empty:
        latest_rank = rh[rh["run_date"] == rh["run_date"].max()].sort_values("rank")
        default = latest_rank["industry"].head(8).tolist()
        selected = st.multiselect("选择板块", sorted(rh["industry"].unique()), default=default)
        if selected:
            sub = rh[rh["industry"].isin(selected)]
            fig = px.line(
                sub, x="run_date", y="rank", color="industry", markers=True,
                color_discrete_sequence=list(COLORWAY),
                labels={"run_date": "日期", "rank": "排名"},
            )
            fig.update_yaxes(autorange="reversed")
            apply_fig(fig, height=420)
            st.plotly_chart(fig, width="stretch")
        else:
            empty("请至少选择一个板块")
    else:
        empty("暂无轮动历史（每日运行后自动积累）")

    section("行业联动网络", "高相关板块，阈值可调")
    threshold = st.slider("相关性阈值", 0.6, 0.95, 0.85, 0.05)
    edges = q.load_linkage_edges(DB, threshold=threshold, max_edges=150)
    if not edges.empty:
        import math
        from collections import Counter

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
        fig.add_trace(go.Scatter(
            x=ex, y=ey, mode="lines",
            line={"color": "rgba(139,151,173,0.35)", "width": 1},
            hoverinfo="none",
        ))
        fig.add_trace(go.Scatter(
            x=[pos[n][0] for n in nodes], y=[pos[n][1] for n in nodes],
            mode="markers+text", text=nodes, textposition="middle center",
            textfont={"size": 11, "color": "#E8EEF7"},
            marker={
                "size": [10 + 5 * deg[n] for n in nodes],
                "color": "#4C8DFF",
                "line": {"color": "#0B1220", "width": 1},
            },
            hovertext=[f"{n}<br>连接 {deg[n]} 个板块" for n in nodes], hoverinfo="text",
        ))
        apply_fig(fig, height=560)
        fig.update_layout(
            showlegend=False, xaxis={"visible": False}, yaxis={"visible": False},
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
        )
        fig.update_yaxes(scaleanchor="x", scaleratio=1)
        st.plotly_chart(fig, width="stretch")
        st.caption(f"显示 {len(nodes)} 个板块 / {len(edges)} 条高相关边（按相关性取前150条）")
    else:
        empty(f"相关性 ≥ {threshold:.0%} 的板块对暂无")

    section("行业风格轮动时间线", "各类风格占比")
    sh = q.load_style_history(DB)
    if not sh.empty:
        pivot = sh.pivot_table(index="run_date", columns="style", values="n", aggfunc="sum").fillna(0)
        share = pivot.div(pivot.sum(axis=1), axis=0) * 100
        fig = px.area(
            share, labels={"value": "占比%", "run_date": "日期", "style": "风格"},
            color_discrete_sequence=list(COLORWAY),
        )
        apply_fig(fig, height=380)
        st.plotly_chart(fig, width="stretch")
    else:
        empty("暂无风格历史（每日运行后自动积累）")


def page_short():
    _health_line()
    page_header("短线轨")
    temp = q.load_temperature(DB)
    if not temp.empty:
        c1, c2 = st.columns(2)
        c1.metric("市场温度", f"{temp.iloc[0]['score']:.0f}/100")
        c2.metric("行业宽度", f"{temp.iloc[0]['profit_effect']:.0%}")
    section("行业相对强度")
    _bar(q.load_strength(DB), "obj", "rs", "行业相对强度 RS")
    section("资金属性 / 风格")
    _show(q.load_capital(DB))
    section("高相关行业对")
    _show(q.load_linkage(DB))
    section("当日短线信号")
    sdf = _safe_df(q.load_signals, DB, horizon="short")
    if sdf.empty:
        empty("暂无短线信号")
    else:
        _show(sdf, "signals")


def page_mid():
    _health_line()
    page_header("中线轨")
    section("周线相对强度")
    _bar(q.load_weekly(DB), "obj", "rs", "周线相对强度 RS")
    section("拥挤度")
    _show(q.load_crowding(DB))
    mdf = _safe_df(q.load_signals, DB, horizon="mid", session="daily")
    for title, block in mid_quadrant_blocks(mdf):
        section(title)
        if block.empty:
            empty("无")
        else:
            _show(block, "signals")
    section("宏观流动性加工")
    _show(q.load_macro(DB))


def page_mid_compare():
    """中期比价工作台：并排事实卡 + 证据/缺口 + 人工比较组 + 入池/建卡。"""
    import json as _json

    _health_line()
    page_header("中期比价")
    st.caption("规则生成事实卡；AI 只提炼带来源的消息/公告/舆情。综合买卖由人判断，系统不产出排名。")
    as_of_raw = st.text_input("时点 as_of（YYYY-MM-DD，空=最新已落库时点）", value="")
    as_of = q.resolve_workbench_as_of(DB, as_of_raw)
    st.caption(f"当前工作台时点 {as_of}")
    dimension = st.selectbox(
        "维度筛选",
        ["全部", "strength", "rotation", "valuation", "crowding", "capital", "cycle", "macro"],
    )
    dim = None if dimension == "全部" else dimension
    cards = q.load_fact_cards(DB, as_of=as_of, dimension=dim)
    section("事实卡")
    st.dataframe(cards.drop(columns=["dimensions_json"], errors="ignore"), width="stretch")
    evid_q = st.text_input("按证据编号检索（EVID-YYYYMMDD-xxxx）", value="")
    if evid_q.strip():
        found = q.find_evidence(DB, evid_q.strip())
        if found.get("error"):
            st.warning(found["error"])
        else:
            st.json(found)
    options = cards["obj"].tolist() if not cards.empty else []
    selected = st.multiselect("人工比较组（建议 3–5 个行业）", options, max_selections=5)
    if st.button("深查并落库") and selected:
        try:
            result = q.run_deep_dive(
                DB,
                industries=selected,
                as_of=as_of,
            )
            st.success(
                f"已深查 {len(result['industry_cards'])} 个行业 / "
                f"{len(result['stock_cards'])} 只个股并落库"
            )
        except Exception as exc:
            st.error(str(exc))
    if selected:
        cols = st.columns(len(selected))
        for col, obj in zip(cols, selected):
            sub = cards[cards["obj"] == obj]
            if sub.empty:
                continue
            row = sub.iloc[0]
            with col:
                st.markdown(f"**{obj}**")
                st.caption(f"as_of={row['as_of']} · {row['rule_version']}")
                try:
                    st.json(_json.loads(row["dimensions_json"] or "{}"), expanded=False)
                except Exception:
                    st.write(row["dimensions_json"])
                with st.expander("证据"):
                    st.dataframe(q.load_fact_evidence(DB, int(row["id"])), width="stretch")
                with st.expander("缺口"):
                    st.write(row["missing_json"])
    section("记录结论")
    conclusion = st.text_input("比较结论")
    notes = st.text_area("备注")
    if st.button("写入比较记录") and selected and conclusion:
        rec = q.save_comparison(
            DB,
            as_of=as_of,
            peer_set=selected,
            conclusion=conclusion,
            notes=notes,
        )
        st.success(f"已记录比较 #{rec['id']}")
    hist = q.load_comparisons(DB, as_of=as_of)
    if not hist.empty:
        st.dataframe(hist, width="stretch")
    section("送入候选池 / 机会卡片")
    symbol = st.text_input("股票代码")
    industry = st.text_input("行业（可选）")
    thesis = st.text_area("建卡三句话验证", value="人工比价后认为中期赔率可接受，先观察")
    c1, c2 = st.columns(2)
    if c1.button("送入候选池") and symbol:
        try:
            q.promote_factcard_to_pool(DB, symbol, industry=industry)
            st.success(f"{symbol} 已入池")
        except Exception as exc:
            st.error(str(exc))
    if c2.button("生成机会卡片") and symbol:
        try:
            created = q.promote_factcard_to_card(
                DB, symbol, thesis=thesis, industry=industry,
            )
            st.success(f"卡片 #{created.get('card_id')} {created.get('status')}")
        except Exception as exc:
            st.error(str(exc))


def page_signals():
    _health_line()
    page_header("交易信号")
    st.markdown('<div class="ts-toolbar">', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    horizon = c1.selectbox("horizon", ["全部", "short", "mid"])
    session = c2.selectbox("session", ["全部", "auction", "intraday", "close", "daily"])
    layer = c3.selectbox("layer", ["全部", "watch", "discovery", "market"])
    severity = c4.selectbox("severity", ["全部", "info", "watch", "action"])
    st.markdown("</div>", unsafe_allow_html=True)
    df = _safe_df(
        q.load_signals,
        DB,
        horizon=None if horizon == "全部" else horizon,
        session=None if session == "全部" else session,
        layer=None if layer == "全部" else layer,
        severity=None if severity == "全部" else severity,
    )
    if df.empty:
        empty("暂无交易信号")
        return
    n_act = int((df["severity"] == "action").sum()) if "severity" in df.columns else 0
    n_watch = int((df["severity"] == "watch").sum()) if "severity" in df.columns else 0
    k1, k2, k3 = st.columns(3)
    k1.metric("条数", str(len(df)))
    k2.metric("行动", str(n_act))
    k3.metric("观察", str(n_watch))
    _show(df, "signals")


def page_bigv():
    _health_line()
    page_header("大V画像库")
    st.caption("选人提问。默认有据（必须对上原文）；可切推断/扮演。一期只自动采雪球。")
    left, right = st.columns([1, 2])
    with left:
        with st.form("bigv_reg"):
            name = st.text_input("显示名")
            xueqiu = st.text_input("雪球 ID 或主页")
            if st.form_submit_button("登记并跟随"):
                r = q.register_bigv(DB, name, xueqiu)
                if r.get("ok"):
                    st.success(f"已登记 {r['profile_id']}")
                else:
                    st.error(r.get("error") or "登记失败")
        df = _safe_df(q.load_bigv_profiles, DB)
        if df.empty:
            empty("名单为空。先登记姓名 + 雪球 ID。")
            pid = ""
        else:
            st.dataframe(df, width="stretch")
            labels = [f"{r['name']}（{r['n_opinions']}条）" for r in df.to_dict("records")]
            pick = st.selectbox("当前人物", labels)
            pid = str(df.iloc[labels.index(pick)]["id"])
    with right:
        if not pid:
            empty("先在左侧登记或选人。")
            return
        mode = st.radio("模式", ["有据", "推断/扮演"], horizontal=True)
        key = f"bigv_chat_{pid}"
        if key not in st.session_state:
            st.session_state[key] = []
        question = st.chat_input("问他一个问题")
        if question:
            st.session_state[key].append({"role": "user", "content": question})
            out = q.ask_bigv(DB, pid, question, "infer" if mode.startswith("推断") else "grounded")
            ans = out.get("answer") or out.get("error") or "没有回答"
            st.session_state[key].append({"role": "assistant", "content": ans})
        for msg in st.session_state[key]:
            st.chat_message(msg["role"]).write(msg["content"])


def page_viewpoints():
    _health_line()
    page_header("观点库")
    col = st.selectbox("状态", ["active", "pending_review", "verified", "all"])
    df = q.load_viewpoints(DB, status=None if col == "all" else col)
    _show(df)
    section("准确率（按来源）")
    acc = q.load_accuracy(DB)
    _show(acc)


def _with_db(fn):
    from invest.db import connect as _connect

    conn = _connect(DB)
    try:
        return fn(conn)
    finally:
        conn.close()


def _try_write(ok_msg: str, fn) -> None:
    try:
        _with_db(fn)
        st.success(ok_msg)
    except Exception as exc:
        st.error(str(exc))


def page_discipline():
    _health_line()
    page_header("执行纪律")
    section("动作清单", "每日规则合成的买/卖/等/减；与下方纪律计划不是同一张表。")
    acts = _safe_df(q.load_actions, DB)
    _show(acts, "actions")
    st.caption("标记完成/跳过只改 status，报告只读。")
    ac1, ac2, ac3 = st.columns(3)
    act_date = ac1.text_input("动作日期", value=date.today().isoformat(), key="act_date")
    act_sym = ac2.text_input("动作代码", key="act_sym")
    with ac3:
        if st.button("完成"):
            from invest.actions.persist import mark_action
            _try_write(f"{act_sym} 已标记完成",
                       lambda c: mark_action(c, act_date, act_sym.strip(), "done"))
        if st.button("跳过"):
            from invest.actions.persist import mark_action
            _try_write(f"{act_sym} 已跳过",
                       lambda c: mark_action(c, act_date, act_sym.strip(), "skipped"))
    section("观察名单", "半自动漏斗，不自动入 core。升级走候选池硬门槛。")
    _show(_safe_df(q.load_watch, DB))
    wc1, wc2, wc3 = st.columns(3)
    w_sym = wc1.text_input("观察代码", key="watch_sym")
    w_reason = wc2.text_input("原因", key="watch_reason")
    with wc3:
        if st.button("加入观察"):
            from invest.actions.watch import add_watch
            _try_write(f"{w_sym} 已加入观察",
                       lambda c: add_watch(c, w_sym.strip(), source="user", reason=w_reason))
        if st.button("驳回"):
            from invest.actions.watch import dismiss_watch
            _try_write(f"{w_sym} 已驳回",
                       lambda c: dismiss_watch(c, w_sym.strip()))
        if st.button("升级 track"):
            from invest.actions.watch import promote_watch
            _try_write(f"{w_sym} 已升 track",
                       lambda c: promote_watch(c, w_sym.strip(), level="track"))
    section("评级 → 建议仓位")
    pl = q.load_position_limit(DB)
    c1, c2, c3 = st.columns(3)
    c1.metric("宏观评级", pl.get("macro") or "未评")
    c2.metric("市场评级", pl.get("market") or "未评")
    c3.metric("建议总仓位上限", f"{pl.get('position_limit', 0.5):.0%}")
    section("评级明细")
    _show(q.load_ratings(DB))
    section("候选池")
    _show(q.load_pool(DB))
    section("活跃交易计划")
    _show(q.load_plans(DB))
    section("交易记录")
    _show(q.load_records(DB))


def page_backtest():
    _health_line()
    page_header("回测")
    _show(q.load_backtests(DB))


def page_status():
    _health_line()
    page_header("数据状态")
    section("覆盖区间")
    _show(q.load_coverage(DB))
    section("最近任务")
    jobs = q.load_jobs(DB)
    _show(jobs, "jobs")
    section("报告回看", "盘前 / 竞价 / 盘后等任务最近一次状态；正文仍走飞书/企微推送。")
    report_jobs = _safe_df(q.load_report_jobs, DB)
    if report_jobs.empty:
        empty("尚无报告任务留痕")
    else:
        _show(report_jobs, "jobs")
        latest = report_jobs.iloc[0]
        detail = str(latest.get("detail") or "").strip()
        if detail:
            st.caption(f"最近一条 {latest.get('job')} · {latest.get('status')}")
            report_block(detail[:1200])


PAGES = {
    "市场总览": page_overview,
    "交易信号": page_signals,
    "轮动与联动": page_rotation,
    "短线轨": page_short,
    "中线轨": page_mid,
    "中期比价": page_mid_compare,
    "观点库": page_viewpoints,
    "大V画像库": page_bigv,
    "执行纪律": page_discipline,
    "回测": page_backtest,
    "数据状态": page_status,
}
if tuple(PAGES) != PAGES_ORDER:
    raise RuntimeError("仪表盘导航顺序与 dashboard.nav.PAGES_ORDER 不一致")

with st.sidebar:
    st.markdown(brand_html(), unsafe_allow_html=True)
    if "nav" not in st.session_state:
        st.session_state.nav = PAGES_ORDER[0]
    for group, names in NAV_GROUPS:
        st.markdown(nav_group_html(group), unsafe_allow_html=True)
        for name in names:
            active = st.session_state.nav == name
            if st.button(name, key=f"nav_{name}", use_container_width=True,
                         type="primary" if active else "secondary"):
                st.session_state.nav = name

choice = st.session_state.get("nav", PAGES_ORDER[0])
if choice not in PAGES:
    choice = PAGES_ORDER[0]
    st.session_state.nav = choice
PAGES[choice]()
