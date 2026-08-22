"""LLM 客户端（OpenAI 兼容），含工具调用循环与用量告警（2026-08-20：取消预算拦截）。

预算策略（2026-08-20 按用户要求改）：
- **不再拦截**：任何 job 都不因用量返回"[预算不足]"，输出不设上限；
- 改为**两个全局告警**（记入 llm_usage 后检查）：
  1. 单次调用超过 SINGLE_CALL_ALERT（默认 20,000 tokens）→ 告警（1 小时限频）；
  2. 当日累计超过 DAILY_TOTAL_ALERT（默认 500,000 tokens）→ 告警（每天一次）。
  告警通过 Notifier（企微+飞书+微信）推送，状态存 data/llm_alert_state.json 防刷屏。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from openai import OpenAI

from invest.config import get_settings

ROOT = Path(__file__).resolve().parents[2]

SINGLE_CALL_ALERT = 20_000     # 单次调用超 2 万 token 视为异常（正常 1-5k）
DAILY_TOTAL_ALERT = 500_000    # 当日累计超 50 万 token 提醒（非管理员限额 100 万的一半）
_SINGLE_ALERT_MIN_INTERVAL = 3600.0
ALERT_STATE_FILE = ROOT / "data" / "llm_alert_state.json"

# 会话内可缓存结果的只读工具（2026-08-21）：相同 (工具, 参数) 在同一轮对话中只执行一次。
# 有副作用/耗时的工具（write_viewpoint / send_direction_hint / request_attribution / run_skill）
# 不在其中，避免重复写入或重复跑长任务。
_READONLY_TOOLS = {
    "query_strength", "query_rotation", "query_temperature", "query_capital",
    "query_linkage", "query_macro", "query_pool", "query_realtime_health",
    "cross_validate", "query_stock_daily", "query_data_freshness",
    "web_search", "web_fetch",
}


class LLMError(Exception):
    pass


def _load_alert_state() -> dict:
    try:
        return json.loads(ALERT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_alert_state(state: dict) -> None:
    try:
        ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALERT_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _alert_text(title: str, detail: str) -> str:
    return f"⚠️【LLM 用量告警】{title}\n{detail}"


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

    def _maybe_alert_usage(self, job: str, tokens: int) -> None:
        """两个全局告警：单次超限（1h 限频）+ 当日累计超限（每天一次）。失败静默。"""
        try:
            state = _load_alert_state()
            now = time.time()
            # 1) 单次调用超限
            if tokens >= SINGLE_CALL_ALERT:
                last = state.get("last_single_alert", 0.0)
                if now - last >= _SINGLE_ALERT_MIN_INTERVAL:
                    _push_alert(
                        f"单次调用 {tokens:,} tokens（超 {SINGLE_CALL_ALERT:,}）",
                        f"job={job}，请检查是否有超长输出/超大工具结果。",
                    )
                    state["last_single_alert"] = now
            # 2) 当日累计超限
            total = 0
            if self.conn is not None:
                row = self.conn.execute(
                    "SELECT SUM(tokens) AS t FROM llm_usage WHERE date=date('now','localtime')"
                ).fetchone()
                total = row["t"] or 0
            if total >= DAILY_TOTAL_ALERT and state.get("daily_alert_date") != time.strftime("%Y-%m-%d"):
                _push_alert(
                    f"当日累计 {total:,} tokens（超 {DAILY_TOTAL_ALERT:,}）",
                    "今日 LLM 用量已超 50 万 token，请注意成本。",
                )
                state["daily_alert_date"] = time.strftime("%Y-%m-%d")
            _save_alert_state(state)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("LLM 用量告警失败: %s", exc)

    def run(
        self,
        system: str,
        user: str,
        tools: list[dict] | None = None,
        dispatch: dict | None = None,
        job: str = "agent",
        max_turns: int = 5,
        max_tokens: int | None = None,
    ) -> str:
        """执行一轮带工具调用的对话，返回最终文本。

        不做用量拦截（2026-08-20）；max_tokens 参数保留但默认不设限（调用方按需使用）。
        2026-08-21：会话内只读工具结果缓存——同一轮对话中相同 (工具, 参数) 的
        只读查询（强度/温度/个股日线/联网搜索等）不重复执行，命中直接复用，
        减少多轮工具调用对同一维度的重复查询（配合 max_turns 收敛降低延迟）。
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        tool_cache: dict[tuple[str, str], str] = {}
        for _turn in range(max_turns):
            try:
                resp = self.client.chat.completions.create(
                    model=self.settings.llm_model,
                    messages=messages,
                    tools=tools or None,
                    temperature=0.2,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                raise LLMError(f"LLM 调用失败: {exc}") from exc
            choice = resp.choices[0]
            msg = choice.message
            if resp.usage:
                tokens = resp.usage.total_tokens or 0
                self._log_usage(job, tokens)
                self._maybe_alert_usage(job, tokens)
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
                cache_key = (tc.function.name, tc.function.arguments or "")
                if fn is None:
                    result = json.dumps({"error": f"未知工具 {tc.function.name}"}, ensure_ascii=False)
                elif tc.function.name in _READONLY_TOOLS and cache_key in tool_cache:
                    result = tool_cache[cache_key]  # 会话内重复查询直接复用
                else:
                    try:
                        result = json.dumps(fn(**args), ensure_ascii=False, default=str)
                    except Exception as exc:
                        result = json.dumps({"error": str(exc)}, ensure_ascii=False)
                    if tc.function.name in _READONLY_TOOLS:
                        if len(tool_cache) >= 64:
                            tool_cache.clear()
                        tool_cache[cache_key] = result
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        # 2026-08-21 兜底：轮数耗尽且最后一条是工具结果时，再让模型做一次"纯文本总结"
        # （不再给 tools），避免把工具 JSON 当最终回复返回给用户。
        if messages[-1].get("role") == "tool":
            try:
                resp = self.client.chat.completions.create(
                    model=self.settings.llm_model,
                    messages=messages + [{"role": "assistant", "content": "请基于以上工具结果给出最终简洁结论。"}],
                    temperature=0.2,
                    max_tokens=max_tokens,
                )
                msg = resp.choices[0].message
                if msg.content:
                    if resp.usage:
                        self._log_usage(job, resp.usage.total_tokens or 0)
                        self._maybe_alert_usage(job, resp.usage.total_tokens or 0)
                    return msg.content
            except Exception:
                pass
        return messages[-1].get("content") or "[达到最大工具轮数]"


def _push_alert(title: str, detail: str) -> None:
    """用量告警推送（企微+飞书+微信；失败静默）。"""
    try:
        from invest.notifier import Notifier

        Notifier().send_text(_alert_text(title, detail), key="llm_alert", min_interval=3600)
    except Exception:
        pass
