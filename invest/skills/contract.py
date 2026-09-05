"""Skill 元数据契约（报告模板 skill 化，2026-08-22）。

每个 skill 模块导出两个成员：
- SKILL: dict —— id / name / kind(report|section) / description / uses / params
- render(**params) -> str —— 纯函数（无副作用：不落库、不推送、不写 job_runs）

params 为说明性参数清单：{'name': '类型, required|optional[, default X]'}，
runner 依此做缺失/多余校验（见 runner.run）。

报告类 skill 另有集中 manifest（必需/可选数据块、时点、覆盖率、降级、时限），
不再把完整性散落在各 render 的 except: pass。
"""
from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("report", "section")


@dataclass(frozen=True)
class ReportManifest:
    """一份报告的数据清单契约。"""

    skill_id: str
    required_blocks: tuple[str, ...]
    optional_blocks: tuple[str, ...]
    slot: str
    min_coverage: float
    degrade_modes: tuple[str, ...]
    max_seconds: float


@dataclass
class CompletenessResult:
    """发送前完整性检查结果。"""

    ok: bool
    status: str
    missing: list[str] = field(default_factory=list)
    degrade: bool = False
    detail: str = ""
    coverage: dict = field(default_factory=dict)


# 覆盖率门槛与行情契约 KEY_COVERAGE_MIN 对齐
_COV = 0.6

REPORT_MANIFESTS: dict[str, ReportManifest] = {
    "a7_auction": ReportManifest(
        skill_id="a7_auction",
        required_blocks=("index_quotes",),
        optional_blocks=("auction_boards", "ladder_quotes", "key_quotes",
                         "core_quotes", "llm_mood"),
        slot="09:25",
        min_coverage=_COV,
        degrade_modes=("facts_only", "omit_llm"),
        max_seconds=90.0,
    ),
    "b1_intraday": ReportManifest(
        skill_id="b1_intraday",
        required_blocks=("index_quotes", "core_quotes"),
        optional_blocks=("etf_quotes", "sector_eod", "fund_flow", "ladder",
                         "llm_mood"),
        slot="intraday",
        min_coverage=_COV,
        degrade_modes=("facts_only", "omit_llm"),
        max_seconds=90.0,
    ),
    "a0_premarket": ReportManifest(
        skill_id="a0_premarket",
        required_blocks=("freshness", "global_snapshot"),
        optional_blocks=("overnight_llm", "news", "halt"),
        slot="08:40",
        min_coverage=0.5,
        degrade_modes=("omit_optional", "omit_llm"),
        max_seconds=120.0,
    ),
    "a3_daily": ReportManifest(
        skill_id="a3_daily",
        required_blocks=("index_quotes",),
        optional_blocks=("etf_quotes", "review", "boards", "plan"),
        slot="22:00",
        min_coverage=0.5,
        degrade_modes=("facts_only", "omit_llm", "omit_optional"),
        max_seconds=180.0,
    ),
}


def get_manifest(skill_id: str) -> ReportManifest:
    """按报告 id 取集中契约；未知 id 抛 KeyError。"""
    if skill_id not in REPORT_MANIFESTS:
        raise KeyError(f"未知报告 manifest: {skill_id}")
    return REPORT_MANIFESTS[skill_id]


def check_completeness(
    manifest: ReportManifest,
    snapshot,
    *,
    elapsed: float = 0.0,
) -> CompletenessResult:
    """按 manifest 校验快照：必需块缺失 → 数据不足；覆盖不足且允许 facts_only → 降级可发。"""
    if elapsed > manifest.max_seconds:
        return CompletenessResult(
            False, "generate_failed", detail="超过最大生成时长",
        )
    missing: list[str] = []
    for name in manifest.required_blocks:
        block = getattr(snapshot, "blocks", {}).get(name)
        if block is None:
            missing.append(name)
            continue
        quotes = getattr(block, "quotes", None)
        payload = getattr(block, "payload", None)
        if quotes is not None:
            # 指数等固定宇宙空结果=缺块；核心池宇宙可为空（无标的）
            if len(quotes) == 0 and name == "index_quotes":
                missing.append(name)
            continue
        if payload is None or payload == "" or payload == [] or payload == {}:
            missing.append(name)
    if missing:
        return CompletenessResult(
            False, "data_insufficient", missing=missing,
            detail="缺少必需数据块: " + ",".join(missing),
        )

    from invest.data.quotes import report_should_degrade

    idx = []
    stocks = []
    blocks = getattr(snapshot, "blocks", {})
    if "index_quotes" in blocks:
        idx = list(getattr(blocks["index_quotes"], "quotes", None) or [])
    for n in ("core_quotes", "ladder_quotes", "key_quotes"):
        if n in blocks:
            stocks.extend(list(getattr(blocks[n], "quotes", None) or []))
    degrade, cov = report_should_degrade(idx, stocks, manifest.min_coverage)
    if degrade and "facts_only" not in manifest.degrade_modes:
        return CompletenessResult(
            False, "data_insufficient", degrade=True, coverage=cov,
            detail="覆盖率不足且不允许降级",
        )
    return CompletenessResult(
        True, "ok", degrade=degrade, coverage=cov,
        detail="ok" if not degrade else "降级为事实列表",
    )


def validate_skill(skill_id: str, module) -> list[str]:
    """校验单个 skill 模块，返回问题列表（空=合法）。"""
    issues: list[str] = []
    skill = getattr(module, "SKILL", None)
    if not isinstance(skill, dict):
        return [f"{skill_id}: 缺少 SKILL 元数据 dict"]
    for meta_field in ("id", "name", "kind", "description", "params"):
        if meta_field not in skill:
            issues.append(f"{skill_id}: SKILL 缺少字段 {meta_field}")
    if skill.get("id") != skill_id:
        issues.append(f"{skill_id}: SKILL['id']={skill.get('id')!r} 与模块 id 不一致")
    if skill.get("kind") not in KINDS:
        issues.append(f"{skill_id}: kind={skill.get('kind')!r} 非法")
    if not callable(getattr(module, "render", None)):
        issues.append(f"{skill_id}: 缺少 render() 函数")
    if skill.get("kind") == "report" and skill_id in REPORT_MANIFESTS:
        try:
            get_manifest(skill_id)
        except KeyError:
            issues.append(f"{skill_id}: 缺少报告 manifest")
    return issues
