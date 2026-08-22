"""Skill 流水线执行器（2026-08-21）：跑 UZI deep-analysis 完整深度分析。

- 目前可执行流水线：UZI-Skill deep-analysis（447 文件，多维数据→LLM 多轮→HTML 报告）；
- serenity / youzi / stock-analysis 为方法论文档（无独立脚本），由 CHAT_SYSTEM 注入方法论；
- subprocess 调用，注入 DeepSeek OpenAI 兼容凭据（OPENAI_API_KEY/BASE_URL），
  --no-browser 避免开浏览器，--depth lite/medium/deep 控制耗时；
- 输出：报告摘要（stdout 关键行 + HTML 报告路径），截断控制 token。
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
UZI_RUN = ROOT / "tools" / "hermes_skills" / "UZI-skill" / "skills" / "deep-analysis" / "run.py"
UZI_REPORTS = ROOT / "tools" / "hermes_skills" / "UZI-skill" / "skills" / "deep-analysis" / "scripts" / "reports"

_TIMEOUTS = {"lite": 600, "medium": 900, "deep": 1200}
_MAX_SUMMARY = 1500


def run_skill(symbol: str, depth: str = "lite", no_browser: bool = True) -> dict:
    """跑 UZI deep-analysis：返回 {ok, report_path?, summary?, stdout?, error?}。"""
    if not UZI_RUN.exists():
        return {"ok": False, "error": f"UZI run.py 不存在: {UZI_RUN}"}
    depth = depth if depth in _TIMEOUTS else "lite"
    from invest.config import get_settings

    settings = get_settings()
    env = dict(__import__("os").environ)
    if settings.llm_api_key:
        env["OPENAI_API_KEY"] = settings.llm_api_key
        env["OPENAI_BASE_URL"] = settings.llm_base_url or "https://api.deepseek.com/v1"
    env["UZI_NO_UPDATE_CHECK"] = "1"  # 跳过自动更新检查，避免卡网络
    env["UZI_LEGACY"] = "1"  # 2026-08-21：走 rrt 老路径（单进程），规避受限环境 multiprocessing 管道限制
    cmd = [sys.executable, str(UZI_RUN), str(symbol), "--depth", depth]
    if no_browser:
        cmd.append("--no-browser")
    logger.info("run_skill 启动: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUTS[depth],
                              encoding="utf-8", errors="replace", env=env, cwd=str(UZI_RUN.parent))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"深度分析超时（>{_TIMEOUTS[depth]}s），请换 lite 档"}
    except Exception as exc:
        logger.warning("run_skill 异常: %s", exc)
        return {"ok": False, "error": f"启动失败: {type(exc).__name__}: {exc}"}

    stdout = (proc.stdout or "") + (proc.stderr or "")
    # 定位报告路径（UZI 两种输出格式：📄 报告路径 / [ok] standalone report）
    report_path = ""
    m = re.search(r"报告路径[:：]\s*(\S+\.html)", stdout) or re.search(r"standalone report[:：]\s*(\S+\.html)", stdout)
    if m:
        report_path = m.group(1)
    if not report_path:
        # 兜底：找最新生成的 standalone 报告
        cand = sorted(UZI_REPORTS.glob(f"*{symbol.replace('.', '')}*/full-report-standalone.html"),
                      key=lambda p: p.stat().st_mtime, reverse=True) if UZI_REPORTS.exists() else []
        if cand:
            report_path = str(cand[0])
    # 摘要：stdout 里关键行
    summary = _extract_summary(stdout)
    # ok 判定：以"报告已生成"为准（UZI 可能因 playwright 缺失等返回非零但报告完好）
    ok = bool(report_path) or ("综合评分" in summary or "Task" in summary)
    return {
        "ok": ok,
        "report_path": report_path,
        "summary": summary[:_MAX_SUMMARY],
        "returncode": proc.returncode,
        "error": None if ok else (stdout[-800:] or f"退出码 {proc.returncode}"),
    }


def _extract_summary(stdout: str) -> str:
    """从 stdout 提取报告要点：优先取结论段/关键 print 行。"""
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    keep: list[str] = []
    for ln in lines:
        if any(k in ln for k in ("报告", "结论", "评分", "综合", "verdict", "✅", "📄", "完成", "失败", "❌", "⚠️")):
            keep.append(ln)
    return "\n".join(keep[-12:]) if keep else stdout[-500:]
