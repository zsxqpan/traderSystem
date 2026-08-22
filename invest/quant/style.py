"""市场风格 / 结构性行情判断：多指数相对强度 → 风格结论。

2026-08-16 新增（v3 结构性行情分析）：用 上证50/沪深300/中证500/中证1000/
科创50/创业板指/北证50 的相对强度，判断当前市场风格（大盘/小盘/成长/价值、
主板/双创/北交所）与结构性行情方向。所有指数以沪深300 为基准计算相对强度，
与 quant_strength(obj_type='index') 口径一致。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import get_params
from .strength import calc_momentum, calc_rs, calc_trend_stage

# 指数分组（用于风格归类）
_INDEX_GROUPS = {
    "大盘": ["000016", "000300"],
    "中盘": ["000905"],
    "小盘": ["000852"],
    "成长/双创": ["000688", "399006"],
    "北交所": ["899050"],
}

_INDEX_NAMES = {
    "000016": "上证50",
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000688": "科创50",
    "399006": "创业板指",
    "899050": "北证50",
}


def _fmt_date(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def compute_style(
    closes: pd.DataFrame,
    benchmark: pd.Series,
    params: dict | None = None,
) -> dict:
    """多指数风格判断。

    closes: date×index_code 收盘价（含至少沪深300 作为基准，越多越好）；
    benchmark: 沪深300 收盘序列。
    返回 dict：
      run_date      分析日期
      index_strength 各指数 {code: {rs, momentum, trend_stage, name}}
      style         风格结论（dict）
    """
    params = params or get_params("strength")
    windows = params["rs_windows"]
    weights = params["rs_weights"]

    index_strength: dict[str, dict] = {}
    for code in closes.columns:
        close = closes[code].dropna()
        if len(close) < 30:
            continue
        rs = calc_rs(close, benchmark, windows, weights)
        mom = calc_momentum(close, params["momentum_windows"])
        stage = calc_trend_stage(close)
        index_strength[code] = {
            "name": _INDEX_NAMES.get(code, code),
            "rs": rs,
            "momentum": mom,
            "trend_stage": stage,
        }

    if not index_strength:
        return {"run_date": None, "index_strength": {}, "style": {}}

    run_date = _fmt_date(closes.index.max())

    # ---- 风格结论 ----
    def _group_avg(codes: list[str], key: str) -> float | None:
        vals = [index_strength[c][key] for c in codes if c in index_strength and not np.isnan(index_strength[c][key])]
        return float(np.mean(vals)) if vals else None

    small = _group_avg(["000852", "000905"], "rs")
    large = _group_avg(["000016", "000300"], "rs")
    growth = _group_avg(["000688", "399006"], "rs")
    beijing = _group_avg(["899050"], "rs")

    # 大盘 vs 小盘
    if small is not None and large is not None:
        if small - large > 0.02:
            size_style = "小盘强于大盘（小盘占优）"
        elif large - small > 0.02:
            size_style = "大盘强于小盘（大盘占优）"
        else:
            size_style = "大小盘均衡"
    else:
        size_style = "数据不足"

    # 成长 vs 价值（用成长指数相对沪深300 的 RS 符号）
    if growth is not None:
        if growth > 0.02:
            growth_style = "成长占优（双创强于沪深300）"
        elif growth < -0.02:
            growth_style = "价值/防御占优（双创弱于沪深300）"
        else:
            growth_style = "成长价值均衡"
    else:
        growth_style = "数据不足"

    # 北交所热度
    if beijing is not None:
        if beijing > 0.03:
            bj_style = "北交所活跃（北证50 显著强于沪深300）"
        elif beijing < -0.03:
            bj_style = "北交所低迷"
        else:
            bj_style = "北交所平淡"
    else:
        bj_style = "数据不足"

    # 结构性行情强度：各指数 RS 的标准差（越大 = 分化越明显 = 越结构性）
    rs_vals = [v["rs"] for v in index_strength.values() if not np.isnan(v["rs"])]
    dispersion = float(np.std(rs_vals)) if len(rs_vals) > 1 else 0.0
    if dispersion > 0.05:
        structure = "强结构性行情（指数分化明显，重选方向）"
    elif dispersion > 0.02:
        structure = "结构性行情（分化中等，方向需精选）"
    else:
        structure = "普涨/普跌为主（指数同步，重仓位择时）"

    # 相对强弱排序
    ranked = sorted(
        ((c, v) for c, v in index_strength.items() if not np.isnan(v["rs"])),
        key=lambda kv: kv[1]["rs"],
        reverse=True,
    )
    ranking = [
        {"code": c, "name": v["name"], "rs": round(v["rs"], 4),
         "momentum": round(v["momentum"], 4), "trend_stage": v["trend_stage"]}
        for c, v in ranked
    ]

    style = {
        "size": size_style,
        "growth": growth_style,
        "beijing": bj_style,
        "structure": structure,
        "dispersion": round(dispersion, 4),
        "ranking": ranking,
    }
    return {"run_date": run_date, "index_strength": index_strength, "style": style}


def style_to_text(style_result: dict) -> str:
    """风格结论 → 可读文本（报告/推送用）。"""
    style = style_result.get("style") or {}
    if not style:
        return "指数数据不足，无法判断市场风格"
    lines = [
        (f"【市场风格】{style.get('size', '')}；{style.get('growth', '')}；"
        f"{style.get('beijing', '')}；{style.get('structure', '')}"),
        "【指数强弱榜】",
    ]
    for i, r in enumerate(style.get("ranking", [])[:7], 1):
        lines.append(
            f"  {i}. {r['name']}  RS={r['rs']:+.3f} 动量={r['momentum']:+.3f} {r['trend_stage']}"
        )
    return "\n".join(lines)
