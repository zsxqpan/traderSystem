"""A6 年度复盘推送 skill（1 月 1 日 09:30 推送；save_report 落库留在调度器）。

render 为纯函数：只组装推送摘要文本，不写库。
content 可传入（scheduler 已算过 yearly_review 时避免重复计算）。
"""
from __future__ import annotations

SKILL = {
    "id": "a6_yearly",
    "name": "年度复盘推送",
    "kind": "report",
    "description": "年度复盘推送摘要：回测结论组数（content 可传入避免重复计算）",
    "uses": [],
    "params": {
        "db_path": "str, required",
        "content": "dict, optional, default None（None 时内部自算 yearly_review）",
    },
}


def render(db_path: str, content: dict | None = None) -> str:
    if content is None:
        from invest.db import connect
        from invest.review.yearly import yearly_review

        conn = connect(db_path)
        try:
            content = yearly_review(conn)
        finally:
            conn.close()
    return f"年度复盘已生成: {len(content['backtest_summary'])} 组回测结论待检视"
