# -*- coding: utf-8 -*-
"""feishu_ws 网关行为回归（2026-08-18 加固：@提及识别/双保险意图/ack/限频/权限提示）。

全部走 mock，不发真实消息、不连真实网络。
"""
from types import SimpleNamespace

import pytest

from invest.push import feishu_ws


class FakeSettings:
    feishu_app_id = "cli_test"
    feishu_app_secret = "s"
    feishu_chat_id = "oc_test"
    feishu_owner_open_id = "ou_owner"
    feishu_bot_open_id = "ou_bot"
    llm_api_key = "sk-test"


@pytest.fixture(autouse=True)
def _fake_settings(monkeypatch):
    monkeypatch.setattr(feishu_ws, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(feishu_ws, "_bot_open_id_cache", "ou_bot")
    feishu_ws._last_reply_at.clear()
    monkeypatch.setattr(feishu_ws, "_nonadmin_budget_exceeded", lambda: False)


def _msg(text: str, mentions=(), msg_type="text", chat_id="oc_test", chat_type="group"):
    return SimpleNamespace(
        chat_id=chat_id,
        chat_type=chat_type,
        content=f'{{"text": "{text}"}}',
        message_type=msg_type,
        mentions=[SimpleNamespace(id=m) for m in mentions],
    )


def _sender(open_id: str, sender_type="user"):
    return SimpleNamespace(
        sender_id=SimpleNamespace(open_id=open_id), sender_type=sender_type
    )


def _event(sender_id: str, text: str, mentions=(), sender_type="user", chat_type="group"):
    return SimpleNamespace(
        event=SimpleNamespace(
            message=_msg(text, mentions, chat_type=chat_type),
            sender=_sender(sender_id, sender_type),
        )
    )


# ---------- 意图判定：关键词规则 + LLM 双保险 ----------

def _patch_llm(monkeypatch, answer="no", exc: Exception | None = None):
    class FakeLLM:
        def __init__(self, *a, **k):
            pass

        def run(self, **k):
            if exc is not None:
                raise exc
            return answer

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)


def test_intent_keyword_goes_to_llm(monkeypatch):
    """2026-08-18：去掉关键词触发——含关键词也必须走 LLM 语义判定。"""
    called = {"n": 0}

    class FakeLLM:
        def __init__(self, *a, **k):
            called["n"] += 1

        def run(self, **k):
            return "yes"

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)
    assert feishu_ws._is_report_request("来一份盘中报告") is True
    assert called["n"] == 1  # 关键词不再直接短路，LLM 被调用
    # LLM 说 no → 即使含关键词也不触发
    _patch_llm(monkeypatch, answer="no")
    assert feishu_ws._is_report_request("盘中报告") is False


def test_intent_llm_no(monkeypatch):
    """LLM 语义说 no → 不触发（无关键词兜底）。"""
    _patch_llm(monkeypatch, answer="no")
    assert feishu_ws._is_report_request("晚上一起吃个饭") is False


def test_intent_llm_failure_returns_false(monkeypatch):
    """LLM 抛异常 → 返回 False（不再回落关键词，纯语义）。"""
    _patch_llm(monkeypatch, exc=RuntimeError("timeout"))
    assert feishu_ws._is_report_request("盘面怎么样") is False
    assert feishu_ws._is_report_request("随便聊聊") is False


# ---------- @ 提及识别 ----------

def test_mentioned_detection():
    assert feishu_ws._is_mentioned(_msg("hi", mentions=["ou_bot"])) is True
    assert feishu_ws._is_mentioned(_msg("hi", mentions=["ou_other"])) is False
    assert feishu_ws._is_mentioned(_msg("hi", mentions=[])) is False


def test_mentioned_detection_dict_id():
    """真实事件里 Mention.id 是 dict {"open_id": ...}（lark-oapi 不转换类型）。"""
    # _msg 会把每个 mention 包成 SimpleNamespace(id=<原始值>)，所以传原始 dict 即可
    assert feishu_ws._is_mentioned(_msg("hi", mentions=[{"open_id": "ou_bot", "union_id": "on_bot", "user_id": ""}])) is True
    assert feishu_ws._is_mentioned(_msg("hi", mentions=[{"open_id": "ou_other"}])) is False
    # union_id 命中也算
    assert feishu_ws._is_mentioned(_msg("hi", mentions=[{"union_id": "ou_bot"}])) is True


def test_mentioned_detection_userid_object():
    """实测 lark-oapi MentionEvent.id 是 UserId 对象（带 .open_id 属性）。"""
    user_id = SimpleNamespace(open_id="ou_bot", user_id="", union_id="on_bot")
    assert feishu_ws._is_mentioned(_msg("hi", mentions=[user_id])) is True
    other = SimpleNamespace(open_id="ou_other", user_id=None, union_id=None)
    assert feishu_ws._is_mentioned(_msg("hi", mentions=[other])) is False


def test_extract_text_post_type():
    """@ 消息可能是富文本 post 类型，要能提取出纯文本。"""
    content = '{"title":"","content":[[{"tag":"text","text":"来一份"},{"tag":"text","text":"盘中报告"}]]}'
    assert feishu_ws._extract_text(content, "post") == "来一份盘中报告"


# ---------- 事件路由：管理员 / 非管理员 / 机器人 ----------

def test_owner_mentioned_report_flow(monkeypatch):
    """管理员 @ + 语义识别为报告请求 → ack + 报告。"""
    _patch_llm(monkeypatch, answer="yes")
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: "【盘中报告】正文")
    feishu_ws._handle_event(_event("ou_owner", "来一份盘中报告", mentions=["ou_bot"]))
    assert sent[0].startswith("⏳")
    assert "【盘中报告】正文" in sent[1]


def test_owner_mentioned_chat_gets_help(monkeypatch):
    """管理员 @ 但语义判定非报告 + 问候 → 帮助提示（零 token 本地判定）。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    _patch_llm(monkeypatch, answer="no")
    feishu_ws._handle_event(_event("ou_owner", "在吗", mentions=["ou_bot"]))
    assert sent and "在的" in sent[0]


def test_owner_mentioned_agent_chat(monkeypatch):
    """管理员群内 @ 非报告非问候 → 会话 Agent 回答（任意消息都响应）。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    _patch_llm(monkeypatch, answer="no")
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="feishu_chat", skill="": "今天半导体板块资金净流入居前（工具数据）。")
    feishu_ws._handle_event(_event("ou_owner", "今天半导体怎么样", mentions=["ou_bot"]))
    assert sent and "半导体" in sent[0]


def test_owner_semantic_without_mention(monkeypatch):
    """管理员不 @：语义识别为报告请求 → 触发报告；语义非报告 → 不回复。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: "R")
    _patch_llm(monkeypatch, answer="yes")
    feishu_ws._handle_event(_event("ou_owner", "现在行情怎么样"))
    assert len(sent) == 2
    sent.clear()
    _patch_llm(monkeypatch, answer="no")
    feishu_ws._handle_event(_event("ou_owner", "盘中报告"))  # 含关键词但语义 no → 不触发
    assert sent == []


def test_non_owner_mentioned_report_gets_public(monkeypatch):
    """非管理员 @ + 语义报告请求 → 公开版报告（public=True，无持仓警戒）。"""
    _patch_llm(monkeypatch, answer="yes")
    calls = {"public": None}
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report",
        lambda public=False, brief=True: calls.__setitem__("public", public) or "【公开版报告】")
    feishu_ws._handle_event(_event("ou_other", "来一份盘中报告", mentions=["ou_bot"]))
    assert calls["public"] is True
    assert sent[0].startswith("⏳")
    assert "【公开版报告】" in sent[1]


def test_non_owner_mentioned_agent_chat(monkeypatch):
    """非管理员 @ 非报告消息 → 会话 Agent 回答（未超限）。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    _patch_llm(monkeypatch, answer="no")
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="group", skill="": "（工具数据）资金风格偏小盘成长。")
    feishu_ws._handle_event(_event("ou_other", "今天什么风格占优", mentions=["ou_bot"]))
    assert sent and "风格" in sent[0]


def test_non_owner_budget_exceeded(monkeypatch):
    """非管理员当日 token 额度用完 → 不调 LLM、不回报告，只回额度提示。"""
    called = {"n": 0}

    class FakeLLM:
        def __init__(self, *a, **k):
            called["n"] += 1

        def run(self, **k):
            return "yes"

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)
    monkeypatch.setattr(feishu_ws, "_nonadmin_budget_exceeded", lambda: True)
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    feishu_ws._handle_event(_event("ou_other", "来一份盘中报告", mentions=["ou_bot"]))
    assert called["n"] == 0  # 未消耗 token
    assert sent and "额度" in sent[0] and "100万" in sent[0]


def test_non_owner_not_mentioned_ignored(monkeypatch):
    """非管理员且未 @ → 忽略（不打扰群聊）。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    feishu_ws._handle_event(_event("ou_other", "今天天气不错"))
    assert sent == []


def test_bot_self_message_ignored(monkeypatch):
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    feishu_ws._handle_event(_event("ou_bot", "我发的", sender_type="bot"))
    assert sent == []


def test_rate_limit(monkeypatch):
    """同一发送者 30s 内重复请求 → 限频跳过。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: "R")
    feishu_ws._last_reply_at.clear()
    _patch_llm(monkeypatch, answer="yes")
    feishu_ws._handle_event(_event("ou_owner", "盘中报告", mentions=["ou_bot"]))
    n1 = len(sent)
    feishu_ws._handle_event(_event("ou_owner", "再来一份盘中报告", mentions=["ou_bot"]))
    assert len(sent) == n1  # 第二次被限频，无新消息


# ---------- 私聊（p2p）----------

def test_agent_chat_skill_self_annotated(monkeypatch):
    """2026-08-18：Skill 由大模型语义自选并在回复文本里自标注，系统原样发送。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    _patch_llm(monkeypatch, answer="no")
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="feishu_chat": "（工具数据）情绪周期主升，短线可打板。\n↘ 已使用 Skill：youzi")
    feishu_ws._handle_event(_event("ou_owner", "今天短线能不能打板，给个操作建议", mentions=["ou_bot"]))
    assert sent and "已使用 Skill：youzi" in sent[0]


def test_dm_owner_report(monkeypatch):
    """私聊：管理员要报告 → 完整报告（含持仓警戒）。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: "【完整报告】")
    _patch_llm(monkeypatch, answer="yes")
    feishu_ws._handle_event(_event("ou_owner", "来一份盘中报告", chat_type="p2p"))
    assert sent[0].startswith("⏳")
    assert "【完整报告】" in sent[1]


def test_dm_owner_chat(monkeypatch):
    """私聊：管理员提问 → 会话 Agent 回答。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    _patch_llm(monkeypatch, answer="no")
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="feishu_chat", skill="": "（工具数据）短线强度靠前的行业有半导体、军工。")
    feishu_ws._handle_event(_event("ou_owner", "最近哪些行业强", chat_type="p2p"))
    assert sent and "半导体" in sent[0]


def test_dm_non_owner_public_report(monkeypatch):
    """私聊：非管理员要报告 → 公开版（public=True），计入 group 限额。"""
    _patch_llm(monkeypatch, answer="yes")
    calls = {"public": None}
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report",
        lambda public=False, brief=True: calls.__setitem__("public", public) or "【公开版】")
    feishu_ws._handle_event(_event("ou_other", "来一份盘中报告", chat_type="p2p"))
    assert calls["public"] is True
    assert "【公开版】" in sent[1]
