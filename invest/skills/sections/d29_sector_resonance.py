"""D29 板块共振 skill（2026-08-23：复用 sector-analysis 方法论，强度∩资金∩联动 三表共振）。

纯规则，无 LLM：行业短线强度 RS TOP15 ∩ 主力净流入 TOP15 → 交集按 RS 排序 TOP n → 补联动伙伴。
任一表空/交集空 → 返回空串。
"""
from __future__ import annotations

SKILL = {
    "id": "d29_sector_resonance",
    "name": "板块共振",
    "kind": "section",
    "description": "板块共振：行业强度 RS TOP15 ∩ 主力净流入 TOP15 → 交集 TOP3（纯规则无 LLM）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "n": "int, optional, default 3",
    },
}


def render(db_path: str, n: int = 3) -> str:
    from invest.db import connect

    conn = connect(db_path)
    try:
        return _sector_resonance(conn, n=n)
    finally:
        conn.close()


def _sector_resonance(conn, n: int = 3) -> str:
    """强度∩资金共振 TOP n。返回文本或空串。"""
    try:
        import pandas as pd

        strength = pd.read_sql_query(
            """SELECT obj, rs FROM quant_strength
               WHERE obj_type='industry' AND period='short'
                 AND run_date=(SELECT MAX(run_date) FROM quant_strength
                               WHERE obj_type='industry' AND period='short')
               ORDER BY rs DESC LIMIT 15""",
            conn,
        )
        flow = pd.read_sql_query(
            """SELECT industry, main_net FROM sector_fund_flow
               WHERE date=(SELECT MAX(date) FROM sector_fund_flow)
               ORDER BY main_net DESC LIMIT 15""",
            conn,
        )
        if strength.empty or flow.empty:
            return ""
        merged = strength.merge(flow, left_on="obj", right_on="industry")
        if merged.empty:
            return ""
        merged = merged.sort_values("rs", ascending=False).head(n)
        lines = []
        for _, r in merged.iterrows():
            ind = r["obj"]
            partner = _linkage_partner(conn, ind)
            line = (f"  {ind} rs{float(r['rs']):+.1%} "
                    f"主力净流入{float(r['main_net']) / 1e8:+.2f}亿")
            if partner:
                line += f" 联动:{partner}"
            lines.append(line)
        return f"【板块共振 TOP{len(lines)}】\n" + "\n".join(lines)
    except Exception:
        return ""


def _linkage_partner(conn, industry: str) -> str:
    """该行业高相关联动伙伴（corr≥0.7 取 top1）。"""
    try:
        row = conn.execute(
            """SELECT a, b FROM quant_linkage
               WHERE run_date=(SELECT MAX(run_date) FROM quant_linkage)
                 AND (a=? OR b=?) AND corr>=0.7
               ORDER BY corr DESC LIMIT 1""",
            (industry, industry),
        ).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    return row["b"] if row["a"] == industry else row["a"]
