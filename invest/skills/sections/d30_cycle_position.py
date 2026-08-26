"""D30 周期行业定位 skill（2026-08-23：复用 stock-cycle 方法论，规则版）。

周期行业白名单 × 中线强度/主力资金/估值分位 → 阶段判定（上行/下行/筑底/过热/震荡）。
纯规则，无 LLM；无数据行业跳过；全部无数据 → 空串。
二期：industry_cycle 表填充后改读 key_indicators 增强。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SKILL = {
    "id": "d30_cycle_position",
    "name": "周期行业定位",
    "kind": "section",
    "description": "周期行业定位：周期白名单 × 中线强度/资金/估值分位 → 上行/下行/筑底/过热（纯规则）",
    "uses": [],
    "params": {
        "db_path": "str, required",
    },
}

# 周期行业白名单（可扩展）
CYCLE_INDUSTRIES = ("有色", "煤炭", "钢铁", "化工", "航运", "养殖", "房地产", "建材", "工程机械")


def render(db_path: str) -> str:
    from invest.db import connect

    conn = connect(db_path)
    try:
        return _cycle_position(conn)
    finally:
        conn.close()


def _cycle_position(conn) -> str:
    lines = []
    for ind in CYCLE_INDUSTRIES:
        try:
            srow = conn.execute(
                """SELECT rs, trend_stage FROM quant_strength
                   WHERE obj_type='industry' AND period='mid' AND obj=?
                     AND run_date=(SELECT MAX(run_date) FROM quant_strength
                                   WHERE obj_type='industry' AND period='mid')""",
                (ind,),
            ).fetchone()
            frow = conn.execute(
                """SELECT main_net FROM sector_fund_flow
                   WHERE date=(SELECT MAX(date) FROM sector_fund_flow) AND industry=?""",
                (ind,),
            ).fetchone()
            vrow = conn.execute(
                """SELECT pe_pct FROM quant_valuation
                   WHERE obj=? AND run_date=(SELECT MAX(run_date)
                                             FROM quant_valuation WHERE obj=?)""",
                (ind, ind),
            ).fetchone()
        except Exception as exc:
            logger.warning("周期行业 %s 数据读取失败: %s", ind, exc)
            continue
        if not srow or srow["rs"] is None:
            continue
        rs = float(srow["rs"])
        net = float(frow["main_net"]) if frow and frow["main_net"] is not None else None
        pe = float(vrow["pe_pct"]) if vrow and vrow["pe_pct"] is not None else None
        stage = _stage(rs, net, pe)
        parts = [f"  {ind}：{stage}", f"中线 rs{rs:+.1%}"]
        if pe is not None:
            parts.append(f"PE分位{pe:.0%}")
        lines.append(" ".join(parts))
    if not lines:
        return ""
    return "【周期行业定位】\n" + "\n".join(lines)


def _stage(rs: float, net: float | None, pe: float | None) -> str:
    """阶段判定：估值分位优先（筑底/过热），其次强度×资金方向。"""
    if pe is not None:
        if pe < 0.30:
            return "筑底"
        if pe > 0.85:
            return "过热"
    if net is None:
        return "上行" if rs > 0 else "下行"
    if rs > 0 and net > 0:
        return "上行"
    if rs < 0 and net < 0:
        return "下行"
    return "震荡"
