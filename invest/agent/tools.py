"""Agent 工具注册表：定量层查询 + 观点写入 + 工单发送。"""
from __future__ import annotations

import functools
import sqlite3

from invest.agent import tickets as ticket_mod


def _query(conn: sqlite3.Connection, sql: str, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


# ---------- 查询工具 ----------
def query_strength(conn, period: str = "short", top: int = 10, obj_type: str = "industry") -> list[dict]:
    return _query(
        conn,
        """SELECT obj, rs, momentum, trend_stage FROM quant_strength
           WHERE period=? AND obj_type=?
             AND run_date = (SELECT MAX(run_date) FROM quant_strength
                             WHERE period=? AND obj_type=?)
           ORDER BY rs DESC LIMIT ?""",
        (period, obj_type, period, obj_type, top),
    )


def query_rotation(conn, top: int = 10) -> list[dict]:
    return _query(conn, "SELECT industry, rank, lead_lag, turnover_share FROM quant_rotation WHERE run_date = (SELECT MAX(run_date) FROM quant_rotation) ORDER BY rank LIMIT ?", (top,))


def query_temperature(conn) -> list[dict]:
    return _query(conn, "SELECT run_date, profit_effect, score FROM quant_temperature ORDER BY run_date DESC LIMIT 1")


def query_capital(conn) -> list[dict]:
    return _query(conn, "SELECT obj, fund_type, style, confidence FROM quant_capital q WHERE run_date = (SELECT MAX(run_date) FROM quant_capital q2 WHERE q2.obj_type = q.obj_type) ORDER BY confidence DESC")


def query_linkage(conn, threshold: float = 0.8, top: int = 10) -> list[dict]:
    return _query(conn, "SELECT a, b, corr, lead FROM quant_linkage WHERE run_date = (SELECT MAX(run_date) FROM quant_linkage) AND corr>=? ORDER BY corr DESC LIMIT ?", (threshold, top))


def query_macro(conn) -> list[dict]:
    return _query(conn, "SELECT date, indicator, value FROM quant_macro ORDER BY date DESC, indicator")


def query_pool(conn) -> list[dict]:
    return _query(conn, "SELECT symbol, level, reason, falsify_condition FROM candidate_pool WHERE out_date IS NULL ORDER BY level, in_date")


# ---------- 写入工具 ----------
def write_viewpoint(
    conn,
    source: str,
    conclusion: str,
    period_tag: str,
    confidence: float,
    evidence: list,
    invalid_condition: str,
    obj_type: str = "",
    obj: str = "",
) -> dict:
    from invest.viewpoints.store import create_viewpoint
    vid = create_viewpoint(
        conn, source=source, conclusion=conclusion, period_tag=period_tag,
        confidence=confidence, evidence=evidence, invalid_condition=invalid_condition,
        obj_type=obj_type, obj=obj,
    )
    return {"viewpoint_id": vid}



def query_realtime_health(conn) -> dict:
    """查询实时行情数据健康状态（数据失效即防守：ok=False 时禁止基于实时价做 P0 决策）。"""
    from invest.config import get_settings
    from invest.data.realtime import realtime_health
    return realtime_health(get_settings().db_path)


def cross_validate(conn, obj: str, obj_type: str = "industry") -> dict:
    """多源交叉验证（A-Stock-Skills 多源校验思想，2026-08-16）。

    对 obj（行业名或个股代码）一次性汇总四个独立维度的最新信号：
    - strength: 相对强度 RS / 趋势阶段 / 动量（短线轨）；
    - capital:   资金属性 / 风格标签；
    - linkage:   高相关联动板块（相关度≥0.7）；
    - valuation: 估值分位（PE/PB）与拥挤度（行业）；
    返回 {obj, dimensions: {...}, n_dimensions, summary}。
    """
    out: dict = {}
    if obj_type == "stock":
        # 个股：强度 + 资金 + 行业维度（不覆盖个股自身维度）
        try:
            rows = _query(
                conn,
                """SELECT obj, rs, trend_stage FROM quant_strength
                   WHERE obj_type='stock' AND period='short' AND obj=?
                     AND run_date = (SELECT MAX(run_date) FROM quant_strength
                                     WHERE obj_type='stock' AND period='short' AND obj=?)""",
                (obj, obj),
            )
            out["strength"] = rows[0] if rows else None
        except Exception:  # noqa: BLE001
            out["strength"] = None
        try:
            rows = _query(
                conn,
                """SELECT fund_type, style, confidence FROM quant_capital
                   WHERE obj_type='stock' AND obj=?
                     AND run_date = (SELECT MAX(run_date) FROM quant_capital
                                     WHERE obj_type='stock' AND obj=?)""",
                (obj, obj),
            )
            out["capital"] = rows[0] if rows else None
        except Exception:  # noqa: BLE001
            out["capital"] = None
        # 个股→行业（手工映射）→ 行业维度放进 industry 子键，不覆盖个股维度
        try:
            from invest.data.industry_map import industry_of
            ind = industry_of(conn, obj)
            if ind:
                out["industry"] = {"name": ind, **_industry_dimensions(conn, ind)}
        except Exception:  # noqa: BLE001
            pass
    else:
        out.update(_industry_dimensions(conn, obj))
    n = sum(1 for v in out.values() if v is not None and v != {})
    return {"obj": obj, "obj_type": obj_type, "dimensions": out, "n_dimensions": n}


def _industry_dimensions(conn, industry: str) -> dict:
    """行业四维度：强度/资金/联动/估值。"""
    res: dict = {}
    try:
        rows = _query(
            conn,
            """SELECT obj, rs, rs5, rs10, rs20, momentum, trend_stage FROM quant_strength
               WHERE obj_type='industry' AND period='short' AND obj=?
                 AND run_date = (SELECT MAX(run_date) FROM quant_strength
                                 WHERE obj_type='industry' AND period='short')""",
            (industry,),
        )
        res["strength"] = rows[0] if rows else None
    except Exception:  # noqa: BLE001
        res["strength"] = None
    try:
        rows = _query(
            conn,
            """SELECT fund_type, style, confidence FROM quant_capital
               WHERE obj_type='industry' AND obj=?
                 AND run_date = (SELECT MAX(run_date) FROM quant_capital
                                 WHERE obj_type='industry' AND obj=?)""",
            (industry, industry),
        )
        res["capital"] = rows[0] if rows else None
    except Exception:  # noqa: BLE001
        res["capital"] = None
    try:
        rows = _query(
            conn,
            """SELECT a, b, corr, lead FROM quant_linkage
               WHERE run_date = (SELECT MAX(run_date) FROM quant_linkage)
                 AND (a=? OR b=?) AND corr>=0.7 ORDER BY corr DESC LIMIT 5""",
            (industry, industry),
        )
        res["linkage"] = rows
    except Exception:  # noqa: BLE001
        res["linkage"] = []
    try:
        rows = _query(
            conn,
            """SELECT pe_pct, pb_pct, crowding, crowding_state FROM quant_valuation
               WHERE obj=?
                 AND run_date = (SELECT MAX(run_date) FROM quant_valuation WHERE obj=?)""",
            (industry, industry),
        )
        res["valuation"] = rows[0] if rows else None
    except Exception:  # noqa: BLE001
        res["valuation"] = None
    return res


def send_direction_hint(conn, direction: str, obj: str, reason: str) -> dict:
    tid = ticket_mod.create_ticket(
        conn, "direction_hint", from_agent="research", to_agent="trade",
        direction=direction, payload={"obj": obj, "reason": reason},
    )
    return {"ticket_id": tid}


def request_attribution(conn, obj: str, reason: str) -> dict:
    tid = ticket_mod.create_ticket(
        conn, "attribution_request", from_agent="trade", to_agent="research",
        payload={"obj": obj, "reason": reason},
    )
    return {"ticket_id": tid}


TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "query_strength", "description": "查询相对强度榜（短线/中线轨；默认行业）", "parameters": {"type": "object", "properties": {"period": {"type": "string", "enum": ["short", "mid"]}, "top": {"type": "integer"}, "obj_type": {"type": "string", "enum": ["industry", "stock"]}}, "required": []}}},
    {"type": "function", "function": {"name": "query_rotation", "description": "查询板块轮动排名与领涨滞后", "parameters": {"type": "object", "properties": {"top": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "query_temperature", "description": "查询市场温度", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "query_capital", "description": "查询行业资金属性与风格", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "query_linkage", "description": "查询行业高相关联动对", "parameters": {"type": "object", "properties": {"threshold": {"type": "number"}, "top": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "query_macro", "description": "查询宏观流动性加工指标", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "query_pool", "description": "查询候选池与关注度", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "write_viewpoint", "description": "写入结构化观点（必须含五要素）", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "conclusion": {"type": "string"}, "period_tag": {"type": "string", "enum": ["micro", "short", "mid", "long"]}, "confidence": {"type": "number"}, "evidence": {"type": "array", "items": {"type": "object"}}, "invalid_condition": {"type": "string"}, "obj_type": {"type": "string"}, "obj": {"type": "string"}}, "required": ["source", "conclusion", "period_tag", "confidence", "evidence", "invalid_condition"]}}},
    {"type": "function", "function": {"name": "send_direction_hint", "description": "投研→交易：方向提示单", "parameters": {"type": "object", "properties": {"direction": {"type": "string"}, "obj": {"type": "string"}, "reason": {"type": "string"}}, "required": ["direction", "obj", "reason"]}}},
    {"type": "function", "function": {"name": "query_realtime_health", "description": "查询实时行情数据健康状态；ok=false 表示行情失效/过期，此时禁止基于实时价格给出开仓或止损决策", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "request_attribution", "description": "交易→投研：归因请求单", "parameters": {"type": "object", "properties": {"obj": {"type": "string"}, "reason": {"type": "string"}}, "required": ["obj", "reason"]}}},
    {"type": "function", "function": {"name": "cross_validate", "description": "多源交叉验证：对某行业或个股汇总四维度最新信号（强度RS/趋势 / 资金风格 / 高相关联动 / 估值PE/PB分位与拥挤度），用于确认方向是否多维度共振", "parameters": {"type": "object", "properties": {"obj": {"type": "string"}, "obj_type": {"type": "string", "enum": ["industry", "stock"], "description": "默认 industry"}}, "required": ["obj"]}}},
]

_IMPLEMENTATIONS = {
    "query_strength": query_strength,
    "query_rotation": query_rotation,
    "query_temperature": query_temperature,
    "query_capital": query_capital,
    "query_linkage": query_linkage,
    "query_macro": query_macro,
    "query_pool": query_pool,
    "write_viewpoint": write_viewpoint,
    "send_direction_hint": send_direction_hint,
    "request_attribution": request_attribution,
    "query_realtime_health": query_realtime_health,
    "cross_validate": cross_validate,
}


def build_dispatch(conn: sqlite3.Connection, source: str = "research") -> dict:
    """把工具绑定到指定数据库连接；写观点工具来源由服务端固定注入。

    LLM 即使传 source 参数也会被覆盖，避免观点来源绕过仲裁与准确率统计。
    """
    out = {name: functools.partial(fn, conn) for name, fn in _IMPLEMENTATIONS.items()}

    def _write(**kwargs):
        kwargs.pop("source", None)
        return write_viewpoint(conn, source=source, **kwargs)

    out["write_viewpoint"] = _write
    return out