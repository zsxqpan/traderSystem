"""A5 月度复盘推送 skill（每月 1 日 09:30 推送；save_report 落库留在调度器）。

render 为纯函数：只组装推送摘要文本，不写库。
content 可传入（scheduler 已算过 monthly_review 时避免重复计算）。
"""
from __future__ import annotations

SKILL = {
    "id": "a5_monthly",
    "name": "月度复盘推送",
    "kind": "report",
    "description": "月度复盘推送摘要：观点命中率/待复盘/环境质量（content 可传入避免重复计算）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "content": "dict, optional, default None（None 时内部自算 monthly_review）",
    },
}


def render(db_path: str, content: dict | None = None) -> str:
    if content is None:
        from invest.db import connect
        from invest.review.monthly import monthly_review

        conn = connect(db_path)
        try:
            content = monthly_review(conn)
        finally:
            conn.close()
    env = content.get("environment_quality", {})
    env_note = ""
    if env.get("verdict") == "warn":
        env_note = "；环境质量告警: " + "；".join(env.get("warnings", []))
    return (
        f"月度复盘: 观点命中率 {content['overall_accuracy'] if content['overall_accuracy'] is not None else '暂无'} | "
        f"待复盘 {content['pending_review']} 条 | 环境质量 {env.get('verdict', 'ok')}{env_note}"
    )
