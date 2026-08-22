"""Skill Runner：按名调用报告/小节 skill（最小职责，2026-08-22）。

run(skill_id, **params) -> str：文本契约（绝大多数 skill）。
run_structured(skill_id, **params) -> str | dict：允许结构化返回（a0 盘前报告）。

1. registry.get(id) —— 未知 id 抛 KeyError（调用方按现状处理）；
2. 按 SKILL['params'] 校验参数（缺失必填 / 未声明参数抛 TypeError）；
3. 调 render(**params)，异常原样上抛。

不做：字符串兜底、限频、job_runs 留痕、推送——全部保留在现有调用方
（scheduler._wrap / feishu_ws._build_intraday_report 等），保证与改造前
行为逐字节一致。
"""
from __future__ import annotations

from invest.skills.registry import get


def _validate(mod, skill_id: str, params: dict) -> None:
    spec = mod.SKILL.get("params") or {}
    required = {k for k, v in spec.items() if "required" in (v or "")}
    missing = required - set(params)
    if missing:
        raise TypeError(f"skill {skill_id} 缺少必填参数: {sorted(missing)}")
    extra = set(params) - set(spec)
    if extra:
        raise TypeError(f"skill {skill_id} 收到未声明参数: {sorted(extra)}")


def run(skill_id: str, **params) -> str:
    mod = get(skill_id)
    _validate(mod, skill_id, params)
    out = mod.render(**params)
    if isinstance(out, dict):
        raise TypeError(f"skill {skill_id} 返回结构化结果，请用 run_structured")
    return out


def run_structured(skill_id: str, **params) -> str | dict:
    """按名调用，允许 skill 返回结构化 dict（a0 盘前报告）或 str。"""
    mod = get(skill_id)
    _validate(mod, skill_id, params)
    return mod.render(**params)
