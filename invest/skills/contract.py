"""Skill 元数据契约（报告模板 skill 化，2026-08-22）。

每个 skill 模块导出两个成员：
- SKILL: dict —— id / name / kind(report|section) / description / uses / params
- render(**params) -> str —— 纯函数（无副作用：不落库、不推送、不写 job_runs）

params 为说明性参数清单：{'name': '类型, required|optional[, default X]'}，
runner 依此做缺失/多余校验（见 runner.run）。
"""
from __future__ import annotations

KINDS = ("report", "section")


def validate_skill(skill_id: str, module) -> list[str]:
    """校验单个 skill 模块，返回问题列表（空=合法）。"""
    issues: list[str] = []
    skill = getattr(module, "SKILL", None)
    if not isinstance(skill, dict):
        return [f"{skill_id}: 缺少 SKILL 元数据 dict"]
    for field in ("id", "name", "kind", "description", "params"):
        if field not in skill:
            issues.append(f"{skill_id}: SKILL 缺少字段 {field}")
    if skill.get("id") != skill_id:
        issues.append(f"{skill_id}: SKILL['id']={skill.get('id')!r} 与模块 id 不一致")
    if skill.get("kind") not in KINDS:
        issues.append(f"{skill_id}: kind={skill.get('kind')!r} 非法")
    if not callable(getattr(module, "render", None)):
        issues.append(f"{skill_id}: 缺少 render() 函数")
    return issues
