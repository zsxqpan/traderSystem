"""报告 Skill 注册表（显式注册，2026-08-22）。

- 显式注册（非自动扫描）：reports/__init__.py 与 sections/__init__.py 在 import
  各 skill 模块后调用 register()；`import invest.skills` 即完成全部注册。
- get / list_skills / validate_all 供 runner 与测试使用。
"""
from __future__ import annotations

from invest.skills.contract import validate_skill

_MODULES: dict[str, object] = {}


def register(skill_id: str, module) -> None:
    """注册一个 skill 模块（id 重复时覆盖并告警）。"""
    if skill_id in _MODULES:
        import logging

        logging.getLogger(__name__).warning("skill 重复注册: %s", skill_id)
    _MODULES[skill_id] = module


def get(skill_id: str):
    """按 id 取 skill 模块；未知 id 抛 KeyError。"""
    if skill_id not in _MODULES:
        raise KeyError(f"未知 skill: {skill_id}")
    return _MODULES[skill_id]


def list_skills(kind: str | None = None) -> list[str]:
    """列出全部（或按 kind 过滤）skill id，排序。"""
    return sorted(
        sid for sid, mod in _MODULES.items()
        if kind is None or mod.SKILL.get("kind") == kind
    )


def validate_all() -> list[str]:
    """全量校验（元数据 + uses 引用），返回问题列表（空=全合法）。"""
    issues: list[str] = []
    for sid, mod in _MODULES.items():
        issues.extend(validate_skill(sid, mod))
        for u in mod.SKILL.get("uses") or []:
            if u not in _MODULES:
                issues.append(f"{sid}: uses 引用不存在的 skill {u}")
    return issues
