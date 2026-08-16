"""比价与因子 v1（TODO 1.2，2026-08-15；[A]4 结构断点 2026-08-15）。

- 主价差：定义（当前值 vs 参照系）、3/5 年历史分位、稳定度 Z 分（中位数/MAD）；
- 参照物选择（v3 5.3 顺序）：同产业链→同类对象→行业等权→中性化基准→全A；
- 结构断点检查（[A]4）：行业分类/会计口径/制度变化时截断历史窗口，
  禁用旧口径分位制造假极值；已知断点（config.yaml breaks）优先，其次统计检测；
- 因子打分 v1：手工 0-5 分制，多因子加权（可配）；
- 因子角色分类：错价 / 修复 / 风险过滤 / 背景（背景因子不占权重，v3 8.1）。
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from invest.config import load_yaml_config

# 因子角色：背景因子权重 0
ROLE_WEIGHT = {"错价": 1.0, "修复": 1.0, "风险过滤": 0.5, "背景": 0.0}


# ---------- 结构断点检查（[A]4） ----------

def load_known_breaks() -> dict[str, list[str]]:
    """读取 config.yaml `breaks` 段：{行业或 symbol: [YYYY-MM-DD, ...]}。

    用于行业分类/会计口径/制度变化等已知结构性断点；命中即截断历史窗口。
    """
    cfg = load_yaml_config()
    return {k: list(v) for k, v in (cfg.get("breaks", {}) or {}).items()}


def _best_block_break(s: pd.Series, k: float, min_seg: int) -> tuple[int | None, float]:
    """在序列中找最强水平跳变点。

    对每个候选切分点 i（左=s[:i]，右=s[i:]），统计量 =
    |中位数差| / 尺度；尺度 = 1.4826 × max(左块平均绝对偏差, 右块平均绝对偏差)，
    并设下限（全局 MAD 的 5%），防止块内全等时除零。
    用「平均绝对偏差」而非「中位绝对偏差」：混块（新旧口径并存）时
    中位数被多数派主导、MAD 会误判为 0，平均值能区分。
    返回 (索引, 统计量)。索引为右块（新口径）起始点。
    """
    n = len(s)
    if n < 2 * min_seg:
        return None, 0.0
    med_g = float(s.median())
    global_mad = float((s - med_g).abs().median())
    floor = max(global_mad * 0.05, 1e-9)
    best_i, best_stat = None, 0.0
    for i in range(min_seg, n - min_seg + 1):
        left, right = s.iloc[:i], s.iloc[i:]
        med_l, med_r = float(left.median()), float(right.median())
        dev_l = float((left - med_l).abs().mean()) if len(left) else 0.0
        dev_r = float((right - med_r).abs().mean()) if len(right) else 0.0
        scale = max(1.4826 * dev_l, 1.4826 * dev_r, floor)
        stat = abs(med_l - med_r) / scale
        # >= 平手取更晚的断点：截断时要保留最新口径（更早的历史一律废弃）
        if stat >= best_stat:
            best_stat, best_i = stat, i
    if best_i is None or best_stat < k:
        return None, 0.0
    return best_i, best_stat


def detect_level_breaks(
    values: pd.Series,
    dates: pd.Series,
    k: float = 3.0,
    window: int | None = None,
    min_samples: int = 40,
) -> list[int]:
    """统计检测水平突变点（步骤变化），递归二分分割。

    思路：块中位数差 / 块内 MAD 尺度，超过 k 视为结构性跳变
    （制度/口径切换）；在右侧继续递归检测更晚的断点。
    返回断点索引列表（索引为右块/新口径起始点，即该点起保留）。
    """
    s = values.dropna()
    if len(s) < 2 * min_samples:
        return []
    breaks: list[int] = []
    stack = [s]
    while stack:
        seg = stack.pop()
        i, stat = _best_block_break(seg, k, min_samples)
        if i is None:
            continue
        breaks.append(seg.index[i])
        # 右侧继续递归（更晚的断点）
        right = seg.iloc[i:]
        if len(right) >= 2 * min_samples:
            stack.append(right)
    return sorted(breaks) if breaks else []


def truncate_at_break(
    values: pd.Series,
    dates: pd.Series,
    entity: str = "",
    known_breaks: dict[str, list[str]] | None = None,
    k: float = 3.0,
    window: int | None = None,
    min_samples: int = 40,
) -> tuple[pd.Series, pd.Series, dict]:
    """截断历史窗口防假极值（[A]4）。

    优先应用已知断点（config.yaml breaks[entity] 中最后一个不晚于最新日期的）；
    无已知断点则统计检测水平突变。返回 (截断后的 values, dates, info)。
    """
    s = values.dropna()
    if len(s) < min_samples:
        return values, dates, {"truncated": False, "note": "样本不足，不截断"}

    dates_s = dates.loc[s.index] if hasattr(dates, "loc") else dates
    known = (known_breaks if known_breaks is not None else load_known_breaks()).get(entity, [])
    known = [d for d in known if str(d) <= str(dates_s.iloc[-1])]
    if known:
        cutoff = max(known)
        mask = dates_s.astype(str) > cutoff
        if mask.sum() >= min_samples:
            return (
                s[mask],
                dates_s[mask],
                {"truncated": True, "cutoff": cutoff, "source": "known",
                 "removed": int((~mask).sum()), "kept": int(mask.sum())},
            )
        return values, dates, {
            "truncated": False, "cutoff": cutoff, "source": "known",
            "note": f"断点后样本不足({int(mask.sum())}<{min_samples})，不截断",
        }

    breaks = detect_level_breaks(s, dates_s, k=k, window=window, min_samples=min_samples)
    if not breaks:
        return values, dates, {"truncated": False, "note": "未检测到结构断点"}
    idx = breaks[-1]
    # 断点索引 = 新口径起始点：保留该点及之后
    pos = s.index.get_loc(idx)
    kept = s.iloc[pos:]
    kept_dates = dates_s.iloc[pos:]
    if len(kept) < min_samples:
        return values, dates, {
            "truncated": False, "source": "detected", "note": "断点后样本不足，不截断",
        }
    return kept, kept_dates, {
        "truncated": True, "cutoff": str(dates_s.iloc[pos]), "source": "detected",
        "removed": pos, "kept": int(len(kept)),
    }


def percentile_rank(series: pd.Series, value: float) -> float | None:
    """value 在 series 中的历史分位（0-1，越低越便宜）。"""
    s = series.dropna()
    if len(s) < 20:
        return None
    return float((s <= value).mean())


def z_score_mad(series: pd.Series, value: float) -> float | None:
    """稳健 Z 分：(value - 中位数) / MAD（中位数绝对偏差，×1.4826 近似 σ）。"""
    s = series.dropna()
    if len(s) < 20:
        return None
    med = float(s.median())
    mad = float((s - med).abs().median())
    if mad <= 0:
        return 0.0
    return (value - med) / (1.4826 * mad)


def spread_analysis(series: pd.Series, current: float) -> dict:
    """主价差分析：当前值 + 历史分位 + Z 分 + 回归锚（中位数）区间。

    series: 历史值序列（如 3-5 年行业 PE / 价格）。"""
    s = series.dropna()
    if len(s) < 20:
        return {"ok": False, "note": "历史样本不足（<20）"}
    pct = percentile_rank(s, current)
    z = z_score_mad(s, current)
    med = float(s.median())
    p25 = float(s.quantile(0.25))
    p75 = float(s.quantile(0.75))
    # 回归锚：历史 40-60% 分位区间（v3：历史分位区间或基本面合理区间）
    anchor_lo = float(s.quantile(0.40))
    anchor_hi = float(s.quantile(0.60))
    # 主价差定义：当前相对锚区间中位（0 表示在锚内）
    spread = (current - med) / med if med else 0.0
    return {
        "ok": True,
        "current": round(current, 4),
        "median": round(med, 4),
        "p25": round(p25, 4),
        "p75": round(p75, 4),
        "anchor_range": [round(anchor_lo, 4), round(anchor_hi, 4)],
        "pct_rank": round(pct, 4) if pct is not None else None,
        "z_score": round(z, 3) if z is not None else None,
        "spread": round(spread, 4),
        "cheap": bool(pct is not None and pct < 0.30),
    }


def industry_pe_spread(
    conn: sqlite3.Connection,
    industry: str,
    years: int = 5,
    check_breaks: bool = True,
) -> dict:
    """行业 PE 主价差：用 industry_valuation 历史 PE 序列。

    check_breaks=True（默认）时先做结构断点检查：行业分类/会计口径变化
    会截断历史窗口，避免旧口径分位制造假极值（[A]4）。
    """
    rows = conn.execute(
        """SELECT date, pe FROM industry_valuation
           WHERE industry=? AND pe IS NOT NULL ORDER BY date""",
        (industry,),
    ).fetchall()
    if not rows:
        return {"ok": False, "note": f"{industry} 无 PE 历史数据"}
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    cutoff = pd.Timestamp.now() - pd.DateOffset(years=years)
    hist = df[df["date"] >= cutoff]
    if hist.empty:
        return {"ok": False, "note": f"{industry} 近 {years} 年无 PE 数据"}
    current = float(df.iloc[-1]["pe"])
    break_info: dict = {"truncated": False}
    if check_breaks:
        hist, _dates, break_info = truncate_at_break(
            hist["pe"].reset_index(drop=True),
            hist["date"].astype(str).reset_index(drop=True),
            entity=industry,
        )
    if hist.empty:
        return {"ok": False, "note": f"{industry} PE 历史经断点截断后为空"}
    result = spread_analysis(hist, current)
    result["industry"] = industry
    result["n_samples"] = int(len(hist))
    result["break"] = break_info
    return result


def price_spread(
    conn: sqlite3.Connection,
    symbol: str,
    years: int = 3,
    check_breaks: bool = True,
) -> dict:
    """个股价格主价差（前复权近似：用收盘价序列，标注 qfq 假设）。

    check_breaks=True（默认）时做结构断点检查（如送转/重大重组前后价格口径变化）。
    """
    rows = conn.execute(
        """SELECT date, close FROM daily_bars
           WHERE symbol=? AND close IS NOT NULL ORDER BY date""",
        (symbol,),
    ).fetchall()
    if not rows:
        return {"ok": False, "note": f"{symbol} 无日线数据"}
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    cutoff = pd.Timestamp.now() - pd.DateOffset(years=years)
    hist = df[df["date"] >= cutoff]
    if hist.empty:
        return {"ok": False, "note": f"{symbol} 近 {years} 年无数据"}
    current = float(df.iloc[-1]["close"])
    break_info: dict = {"truncated": False}
    if check_breaks:
        hist, _dates, break_info = truncate_at_break(
            hist["close"].reset_index(drop=True),
            hist["date"].astype(str).reset_index(drop=True),
            entity=symbol,
        )
    if hist.empty:
        return {"ok": False, "note": f"{symbol} 价格历史经断点截断后为空"}
    result = spread_analysis(hist, current)
    result["symbol"] = symbol
    result["n_samples"] = int(len(hist))
    result["break"] = break_info
    result["note"] = "价格口径为未复权收盘价，分位/Z 分受除权影响（qfq 数据接入后校准）"
    return result


# ---------- 因子打分 v1 ----------

def factor_score(
    factors: list[dict],
) -> dict:
    """手工 0-5 分因子打分（v1）。

    factors: [{name, score(0-5), role(错价/修复/风险过滤/背景), weight?}]
    总分 = Σ(score × role_weight) / Σ(role_weight)（背景因子权重 0，不占权重）。
    返回 {total, per_factor, roles}。
    """
    if not factors:
        return {"ok": False, "total": 0.0, "note": "无因子"}
    total_w = 0.0
    acc = 0.0
    per = []
    roles: dict[str, int] = {}
    for f in factors:
        name = f["name"]
        score = max(0.0, min(5.0, float(f["score"])))
        role = f.get("role", "错价")
        weight = float(f.get("weight", ROLE_WEIGHT.get(role, 1.0)))
        if role == "背景":
            weight = 0.0  # 背景因子不占权重（v3 8.1）
        roles[role] = roles.get(role, 0) + 1
        acc += score * weight
        total_w += weight
        per.append({"name": name, "score": score, "role": role, "weight": weight})
    total = acc / total_w if total_w > 0 else 0.0
    return {
        "ok": True,
        "total": round(total, 2),
        "grade": "S" if total >= 4.2 else ("A" if total >= 3.5 else ("B" if total >= 2.8 else "C")),
        "per_factor": per,
        "roles": roles,
    }


def suggest_reference(industry: str, conn: sqlite3.Connection | None = None) -> str:
    """参照物选择（v3 5.3 顺序）：同产业链→同类对象→行业等权→中性化基准→全A。

    v1 简化：有行业 PE 数据用行业等权，否则全A；返回建议文本。
    """
    if conn is not None:
        row = conn.execute(
            "SELECT COUNT(*) c FROM industry_valuation WHERE industry=?", (industry,)
        ).fetchone()
        if row and row["c"] > 0:
            return f"行业等权（{industry} PE 历史可作参照）"
    return "全A（无行业估值数据时用中性化基准）"


# ---------- 榜单降级为「发现器」（[A]5，v3 7.3） ----------

def mispricing_necessary(
    spread_result: dict,
    cheap_pct: float = 0.30,
    min_z: float | None = None,
) -> tuple[bool, str]:
    """过错价必要条件：综合分排名不直接入池，候选必须过错价必要条件。

    规则（v3 7.3）：主价差可用 且 历史分位 < cheap_pct（便宜）；
    若分位不可用（样本不足），退而求其次要求 z_score 显著为负（低于锚）。

    返回 (是否满足, 说明)。
    """
    if not spread_result.get("ok"):
        return False, f"主价差不可用: {spread_result.get('note', '')}"
    pct = spread_result.get("pct_rank")
    z = spread_result.get("z_score")
    if pct is not None and pct < cheap_pct:
        return True, f"历史分位 {pct:.2%} < {cheap_pct:.0%}，具备错价必要条件"
    if pct is None or (min_z is not None and z is not None and z < min_z):
        if z is not None and z < 0:
            return True, f"分位不可用但 Z={z:.2f} 显著低于锚，具备错价必要条件"
    return False, f"不满足过错价必要条件（分位 {pct if pct is None else f'{pct:.2%}'}，Z={z if z is None else f'{z:.2f}'}）"


def discover_eligible(
    conn: sqlite3.Connection,
    candidate: str,
    industry: str = "",
    spread: dict | None = None,
    cheap_pct: float = 0.30,
) -> dict:
    """发现器准入判断：候选入池前必须过错价必要条件（[A]5）。

    candidate: 标的代码或行业名（有 industry_valuation 的按行业算 PE 价差）；
    spread: 若外部已算好主价差则直接传入（优先）。
    返回 {eligible, reason, spread}；不满足时 reason 说明原因。
    """
    if spread is None:
        if industry:
            spread = industry_pe_spread(conn, industry)
        else:
            spread = price_spread(conn, candidate)
    ok, reason = mispricing_necessary(spread, cheap_pct=cheap_pct)
    return {"eligible": ok, "reason": reason, "spread": spread}
