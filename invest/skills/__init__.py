"""报告模板 Skill 引擎（2026-08-22）。

- contract.py：SKILL 元数据契约与校验
- registry.py：显式注册表（get / list_skills / validate_all）
- runner.py：按名调用（最小职责，异常原样上抛）
- reports/：7 个报告 skill（A1-A6、B1）
- sections/：23 个小节 skill（D1-D23）

`import invest.skills` 即完成全部注册（reports/sections 子包 import 时 register）。
"""
from invest.skills import reports, sections

__all__ = ["reports", "sections"]
