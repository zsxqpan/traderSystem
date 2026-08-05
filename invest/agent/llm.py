"""LLM 客户端（OpenAI 兼容），含预算控制与工具调用循环。"""
from __future__ import annotations

import json
import sqlite3

from openai import OpenAI

from invest.config import get_settings


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, conn: sqlite3.Connection | None = None, settings=None):
        self.settings = settings or get_settings()
        self.conn = conn
        if not self.settings.llm_api_key:
            raise LLMError("未配置 LLM_API_KEY（.env）")
        self.client = OpenAI(
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key,
        )

    def _log_usage(self, job: str, tokens: int) -> None:
        if self.conn is None:
            return
        with self.conn:
            self.conn.execute(
                """INSERT INTO llm_usage(date, job, tokens) VALUES(date('now','localtime'), ?, ?)
                   ON CONFLICT(date, job) DO UPDATE SET tokens = tokens + excluded.tokens""",
                (job, tokens),
            )

    def _budget_ok(self, job: str) -> bool:
        if self.conn is None:
            return True
        row = self.conn.execute(
            "SELECT SUM(tokens) AS t FROM llm_usage WHERE date=date('now','localtime') AND job=?", (job,)
        ).fetchone()
        return (row["t"] or 0) < self.settings.daily_llm_budget_tokens

    def run(
        self,
        system: str,
        user: str,
        tools: list[dict] | None = None,
        dispatch: dict | None = None,
        job: str = "agent",
        max_turns: int = 5,
    ) -> str:
        """执行一轮带工具调用的对话，返回最终文本。"""
        if not self._budget_ok(job):
            return "[预算不足，跳过本轮推理]"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        for _turn in range(max_turns):
            try:
                resp = self.client.chat.completions.create(
                    model=self.settings.llm_model,
                    messages=messages,
                    tools=tools or None,
                    temperature=0.2,
                )
            except Exception as exc:
                raise LLMError(f"LLM 调用失败: {exc}") from exc
            choice = resp.choices[0]
            msg = choice.message
            if resp.usage:
                self._log_usage(job, resp.usage.total_tokens or 0)
            if not msg.tool_calls:
                return msg.content or ""
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = {}
                fn = (dispatch or {}).get(tc.function.name)
                if fn is None:
                    result = json.dumps({"error": f"未知工具 {tc.function.name}"}, ensure_ascii=False)
                else:
                    try:
                        result = json.dumps(fn(**args), ensure_ascii=False, default=str)
                    except Exception as exc:  # noqa: BLE001
                        result = json.dumps({"error": str(exc)}, ensure_ascii=False)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        return messages[-1].get("content") or "[达到最大工具轮数]"