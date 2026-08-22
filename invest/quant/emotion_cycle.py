"""情绪周期状态机（2026-08-16，借鉴 quantdash-ai-stock 情绪周期 + youzi 游资视角）。

基于 market_emotion（涨停数/最高连板/炸板率）判定 A 股短线情绪四阶段：
冰点 → 启动 → 主升 → 退潮，并输出阶段依据与操作基调。

- emotion_cycle(): 单日或多日情绪 → 阶段 + 依据；
- cycle_series(): 多日序列逐日定级（供趋势判断/日报引用）；
- cycle_guide(): 阶段 → 操作基调（与 youzi-trading skill 口径一致）。
"""
from __future__ import annotations

import pandas as pd

# 四阶段判定阈值（可调，参照游资情绪周期常见口径）
THRESHOLDS = {
    "freeze_limit_up": 40,      # 冰点：涨停数 < 40
    "freeze_max_lianban": 3,    # 冰点：最高连板 <= 3
    "freeze_zhaban_rate": 0.40, # 冰点：炸板率 > 40%
    "boom_limit_up": 80,        # 主升：涨停数 > 80
    "boom_max_lianban": 5,      # 主升：最高连板 >= 5
    "boom_zhaban_rate": 0.25,   # 主升：炸板率 < 25%
    "retreat_zhaban_rate": 0.50,  # 退潮：炸板率 > 50%
    "retreat_drop_vs_ma3": 0.30,  # 退潮：涨停数较 3 日均值回落 > 30%
}

STAGES = ("冰点", "启动", "主升", "退潮")


def _row_data(row: pd.Series) -> dict:
    """从行提取 limit_up_count/max_lianban/zhaban_rate（宽松解析）。"""
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return {
        "limit_up": num(row.get("limit_up_count")),
        "max_lianban": num(row.get("max_lianban")),
        "zhaban_rate": num(row.get("zhaban_rate")),
    }


def _judge(d: dict, prev_avg: float | None = None) -> tuple[str, list[str]]:
    """单日判定：返回 (阶段, 依据列表)。"""
    reasons: list[str] = []
    lu, ml, zr = d["limit_up"], d["max_lianban"], d["zhaban_rate"]
    t = THRESHOLDS

    # 冰点：涨停少 + 连板低 + 炸板高
    if ((lu is not None and lu < t["freeze_limit_up"]) or (zr is not None and zr > t["freeze_zhaban_rate"])) and (ml is not None and ml <= t["freeze_max_lianban"]):
            reasons.append(f"涨停{lu:.0f} 连板最高{ml:.0f} 炸板率{zr:.0%}" if lu and zr else "涨停少/炸板高")
            return "冰点", reasons

    # 退潮：炸板率飙高 或 涨停数较 3 日均值大幅回落
    if zr is not None and zr > t["retreat_zhaban_rate"]:
        reasons.append(f"炸板率{zr:.0%} > {t['retreat_zhaban_rate']:.0%}")
        return "退潮", reasons
    if prev_avg is not None and lu is not None and prev_avg > 0:
        drop = (prev_avg - lu) / prev_avg
        if drop > t["retreat_drop_vs_ma3"]:
            reasons.append(f"涨停{lu:.0f} 较3日均值{prev_avg:.0f}回落{drop:.0%}")
            return "退潮", reasons

    # 主升：涨停多 + 连板高 + 炸板低
    if ((lu is not None and lu > t["boom_limit_up"]) or (ml is not None and ml >= t["boom_max_lianban"])) and (zr is None or zr < t["boom_zhaban_rate"]):
            reasons.append(f"涨停{lu:.0f} 连板最高{ml:.0f} 炸板率{zr:.0%}" if lu and zr else "涨停多/连板高")
            return "主升", reasons

    # 默认启动（涨停/连板回暖但未到主升强度）
    reasons.append("介于冰点与主升之间（涨停/连板回暖）")
    return "启动", reasons


def emotion_cycle(df: pd.DataFrame) -> dict:
    """单日情绪 → 阶段。

    df: 单行或含 date 的多行 market_emotion；取最新一行。
    返回 {date, stage, reasons, guide}。
    """
    if df is None or df.empty:
        return {"stage": "数据不足", "reasons": ["无情绪数据"], "guide": cycle_guide("数据不足")}
    row = df.sort_values("date").iloc[-1] if "date" in df.columns else df.iloc[-1]
    d = _row_data(row)
    # 3 日均值（若有多日）
    prev_avg = None
    if "limit_up_count" in df.columns and len(df) >= 4:
        vals = pd.to_numeric(df["limit_up_count"], errors="coerce").dropna()
        if len(vals) >= 4:
            prev_avg = float(vals.iloc[-4:-1].mean())
    stage, reasons = _judge(d, prev_avg)
    return {
        "date": str(row.get("date", "")) if hasattr(row, "get") else "",
        "stage": stage,
        "reasons": reasons,
        "guide": cycle_guide(stage),
        "data": d,
    }


def cycle_series(df: pd.DataFrame) -> pd.DataFrame:
    """多日情绪序列逐日定级。返回 date/stage/limit_up/max_lianban/zhaban_rate。"""
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame(columns=["date", "stage", "limit_up", "max_lianban", "zhaban_rate"])
    out = []
    d = df.sort_values("date").reset_index(drop=True)
    for i, row in d.iterrows():
        hist = d.iloc[: i + 1]
        res = emotion_cycle(hist)
        rd = _row_data(row)
        out.append({
            "date": str(row["date"]),
            "stage": res["stage"],
            "limit_up": rd["limit_up"], "max_lianban": rd["max_lianban"],
            "zhaban_rate": rd["zhaban_rate"],
        })
    return pd.DataFrame(out)


def cycle_guide(stage: str) -> str:
    """阶段 → 操作基调（与 youzi-trading 口径一致）。"""
    return {
        "冰点": "空仓/轻仓试错：等反包与首板回暖，不接高位",
        "启动": "打首板/低吸卡位：确认后加仓，重仓在确认后",
        "主升": "持龙头/做补涨：龙头不破不卖，梯队完整可加",
        "退潮": "果断止盈：不接飞刀，等冰点再布局",
        "数据不足": "情绪数据缺失，维持中性仓位",
    }.get(stage, "数据不足，维持中性仓位")
