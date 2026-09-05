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
    # 2026-08-22：盘中报告走卡片发送——全局防真实发送（测试只验回退到 send_message）
    monkeypatch.setattr("invest.push.feishu_push.send_card", lambda *a, **k: False)
    # 2026-08-29：_send_intraday_with_receipt 写真实库 delivery_receipts 回执，
    # 同分钟同 chat 槽位已 succeeded 时去重跳过发送 → 每测前清 intraday_report 回执。
    from invest.db import connect as _connect

    try:
        _conn = _connect(str(feishu_ws.ROOT / "data" / "invest.db"))
        try:
            _conn.execute("DELETE FROM delivery_receipts WHERE job='intraday_report'")
            _conn.commit()
        finally:
            _conn.close()
    except Exception:
        pass  # 表未建（未跑过 init_db）时静默


def _report_struct(text: str = "R") -> dict:
    """盘中报告 mock 返回的结构（2026-08-22 b1 结构化后）。"""
    return {"title": "测试盘中报告", "sections": [{"type": "text", "text": text}]}


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


def _event(sender_id: str, text: str, mentions=(), sender_type="user", chat_type="group",
           chat_id="oc_test"):
    return SimpleNamespace(
        event=SimpleNamespace(
            message=_msg(text, mentions, chat_type=chat_type, chat_id=chat_id),
            sender=_sender(sender_id, sender_type),
        )
    )


# ---------- 意图判定：关键词快速判定 + LLM 兜底（2026-08-21 两级） ----------

def _patch_llm(monkeypatch, answer="no", exc: Exception | None = None):
    class FakeLLM:
        def __init__(self, *a, **k):
            pass

        def run(self, **k):
            if exc is not None:
                raise exc
            return answer

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)


def test_intent_keyword_shortcuts_llm(monkeypatch):
    """2026-08-21：关键词快速判定优先——命中直接短路，零 LLM 调用（原 08-18 纯语义决策回退）。"""
    called = {"n": 0}

    class FakeLLM:
        def __init__(self, *a, **k):
            called["n"] += 1

        def run(self, **k):
            return "no"

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)
    assert feishu_ws._is_report_request("来一份盘中报告") is True
    assert feishu_ws._is_report_request("现在行情怎么样") is True
    assert feishu_ws._is_report_request("盘中报告") is True
    assert called["n"] == 0  # 关键词命中不调 LLM


def test_intent_negative_keyword_skips_llm(monkeypatch):
    """负向词（历史/周报/新闻等明确非"当前实时报告"）→ 直接 False，零 LLM。"""
    called = {"n": 0}

    class FakeLLM:
        def __init__(self, *a, **k):
            called["n"] += 1

        def run(self, **k):
            return "yes"

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)
    assert feishu_ws._is_report_request("上周行情怎么样") is False
    assert feishu_ws._is_report_request("来一份周报") is False
    assert feishu_ws._is_report_request("今天有什么政策新闻") is False
    assert called["n"] == 0


def test_intent_stock_code_goes_to_llm(monkeypatch):
    """含 6 位代码且无「报告」的个股查询 → 实时报价，不误判为盘中报告，也不再交 LLM。"""
    called = {"n": 0}

    class FakeLLM:
        def __init__(self, *a, **k):
            called["n"] += 1

        def run(self, **k):
            return "yes"

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)
    assert feishu_ws._is_report_request("600519 现在行情怎么样") is False
    assert called["n"] == 0


def test_intent_unresolved_goes_to_llm(monkeypatch):
    """本地已判 greeting/chat 时不再 LLM 二次判报告。"""
    called = {"n": 0}

    class FakeLLM:
        def __init__(self, *a, **k):
            called["n"] += 1

        def run(self, **k):
            return "yes"

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)
    assert feishu_ws._is_report_request("晚上一起吃个饭") is False
    assert feishu_ws._is_report_request("看看今天市场怎么样") is False
    assert called["n"] == 0


def test_intent_llm_no(monkeypatch):
    """LLM 语义说 no → 不触发（未决消息）。"""
    _patch_llm(monkeypatch, answer="no")
    assert feishu_ws._is_report_request("晚上一起吃个饭") is False


def test_intent_llm_failure_returns_false(monkeypatch):
    """LLM 抛异常 → 未决消息返回 False；关键词命中的仍直接 True，不依赖 LLM。"""
    _patch_llm(monkeypatch, exc=RuntimeError("timeout"))
    assert feishu_ws._is_report_request("随便聊聊") is False
    assert feishu_ws._is_report_request("盘面怎么样") is True


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
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: _report_struct("【盘中报告】正文"))
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
        lambda conn, text, job="feishu_chat", chat_id="", skill="", **kw: "今天半导体板块资金净流入居前（工具数据）。")
    feishu_ws._handle_event(_event("ou_owner", "今天半导体怎么样", mentions=["ou_bot"]))
    assert sent and "半导体" in sent[0]


def test_owner_without_mention_ignored(monkeypatch):
    """2026-08-25：管理员群内未 @ → 一律不回应（含'盘中/报告'字样，不再触发报告）。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: _report_struct("R"))
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="feishu_chat", chat_id="", skill="", **kw: "不应触发")
    feishu_ws._handle_event(_event("ou_owner", "现在行情怎么样"))       # 未 @
    assert sent == []
    feishu_ws._handle_event(_event("ou_owner", "来一份盘中报告"))        # 未 @ 报告请求
    assert sent == []
    feishu_ws._handle_event(_event("ou_owner", "今天聊点盘中数据"))      # 含'盘中'字样
    assert sent == []
    print("test_owner_without_mention_ignored OK")


def test_owner_mentioned_other_not_triggered(monkeypatch):
    """2026-08-25：管理员群内 @ 别人（非机器人）→ 不触发 Agent 回应（移除文本含@兜底）。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="feishu_chat", chat_id="", skill="", **kw: "不应触发")
    feishu_ws._handle_event(_event("ou_owner", "@_user_2 晚上一起吃饭吗", mentions=["ou_other"]))
    assert sent == []  # @ 的是别人 → 不回应
    print("test_owner_mentioned_other_not_triggered OK")


def test_non_owner_mentioned_report_gets_public(monkeypatch):
    """非管理员 @ + 语义报告请求 → 公开版报告（public=True，无持仓警戒）。"""
    _patch_llm(monkeypatch, answer="yes")
    calls = {"public": None}
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report",
        lambda public=False, brief=True: calls.__setitem__("public", public) or _report_struct("【公开版报告】"))
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
        lambda conn, text, job="group", chat_id="", skill="", **kw: "（工具数据）资金风格偏小盘成长。")
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
    """同一发送者限频窗口内重复请求 → 明确反馈，不再静默丢消息。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: _report_struct())
    feishu_ws._last_reply_at.clear()
    _patch_llm(monkeypatch, answer="yes")
    feishu_ws._handle_event(_event("ou_owner", "盘中报告", mentions=["ou_bot"]))
    n1 = len(sent)
    feishu_ws._handle_event(_event("ou_owner", "再来一份盘中报告", mentions=["ou_bot"]))
    assert len(sent) == n1 + 1
    assert any(k in sent[-1] for k in ("频繁", "稍后", "限频", "间隔"))


# ---------- 私聊（p2p）----------

def test_agent_chat_skill_self_annotated(monkeypatch):
    """2026-08-18：Skill 由大模型语义自选并在回复文本里自标注，系统原样发送。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    _patch_llm(monkeypatch, answer="no")
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="feishu_chat", chat_id="", skill="", **kw: "（工具数据）情绪周期主升，短线可打板。\n↘ 已使用 Skill：youzi")
    feishu_ws._handle_event(_event("ou_owner", "今天短线能不能打板，给个操作建议", mentions=["ou_bot"]))
    assert sent and "已使用 Skill：youzi" in sent[0]


def test_should_react_only_mention_or_dm():
    """2026-08-20：只有艾特机器人的群消息/私聊才回 ❤️，普通群消息不回。"""
    assert feishu_ws._should_react(_msg("hi", mentions=["ou_bot"])) is True     # 群内艾特
    assert feishu_ws._should_react(_msg("hi", mentions=[])) is False            # 群内未艾特
    assert feishu_ws._should_react(_msg("hi", mentions=["ou_other"])) is False  # 艾特别人
    assert feishu_ws._should_react(_msg("hi", chat_type="p2p")) is True         # 私聊


def test_dm_owner_report(monkeypatch):
    """私聊：管理员要报告 → 完整报告（含持仓警戒）。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: _report_struct("【完整报告】"))
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
        lambda conn, text, job="feishu_chat", chat_id="", skill="", **kw: "（工具数据）短线强度靠前的行业有半导体、军工。")
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
        lambda public=False, brief=True: calls.__setitem__("public", public) or _report_struct("【公开版】"))
    feishu_ws._handle_event(_event("ou_other", "来一份盘中报告", chat_type="p2p"))
    assert calls["public"] is True
    assert "【公开版】" in sent[1]


# ---------- 2026-08-21：关键词仅用于群聊，私聊始终 LLM 语义判定 ----------

def test_is_report_request_keyword_flag(monkeypatch):
    """私聊与群聊一致：本地规则优先。明确「来一份盘中报告」不依赖 LLM。"""
    called = {"n": 0}

    class FakeLLM:
        def __init__(self, *a, **k):
            called["n"] += 1

        def run(self, **k):
            return "no"

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)
    assert feishu_ws._is_report_request("来一份盘中报告") is True
    assert feishu_ws._is_report_request("来一份盘中报告", keyword=False) is True
    assert called["n"] == 0


def test_group_mention_report_keyword_shortcut(monkeypatch):
    """群内@：关键词命中直接触发报告，即使 LLM 说 no（零 token）。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: _report_struct())
    _patch_llm(monkeypatch, answer="no")
    feishu_ws._handle_event(_event("ou_owner", "来一份盘中报告", mentions=["ou_bot"]))
    assert len(sent) == 2 and "R" in sent[1]


def test_dm_report_needs_llm_yes(monkeypatch):
    """私聊与群聊一致：明确盘中报告走本地规则，LLM 说 no 也不挡。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report", lambda public=False, brief=True: _report_struct())
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="feishu_chat", chat_id="", skill="", **kw: "（会话回答，不触发报告）")
    _patch_llm(monkeypatch, answer="no")
    feishu_ws._handle_event(_event("ou_owner", "来一份盘中报告", chat_type="p2p"))
    assert len(sent) == 2 and "R" in sent[1]


def test_intent_same_for_p2p_and_group(monkeypatch):
    """私聊与群聊对明确意图使用同一套本地规则。"""
    from invest.agent.agents import classify_intent

    text = "600519现价多少"
    assert classify_intent(text) == "realtime_quote"
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr(feishu_ws, "_build_intraday_report",
                        lambda public=False, brief=True: _report_struct("不应发报告"))
    captured = []

    def _fake_chat(conn, text, job="feishu_chat", chat_id="", skill="", **kw):
        captured.append({"job": job, "chat_id": chat_id, "sender_id": kw.get("sender_id", ""),
                         "text": text})
        return "现价 1400 [ev_1]"

    monkeypatch.setattr("invest.agent.agents.run_chat", _fake_chat)
    _patch_llm(monkeypatch, answer="yes")  # 即使 LLM 想判成报告也不该发报告
    feishu_ws._handle_event(_event("ou_owner", text, mentions=["ou_bot"]))
    feishu_ws._last_reply_at.clear()
    feishu_ws._handle_event(_event("ou_owner", text, chat_type="p2p"))
    assert captured
    assert any("1400" in (s or "") for s in sent)
    assert all("不应发报告" not in (s or "") for s in sent)
    assert len(captured) == 2
    assert captured[0]["sender_id"] == "ou_owner"
    assert captured[1]["sender_id"] == "ou_owner"


def test_chat_rate_limit_feedback(monkeypatch):
    """普通问答限频也要给明确反馈，不能静默丢。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="feishu_chat", chat_id="", skill="", **kw: "第一答")
    _patch_llm(monkeypatch, answer="no")
    feishu_ws._last_reply_at.clear()
    feishu_ws._handle_event(_event("ou_owner", "今天半导体怎么样", mentions=["ou_bot"]))
    feishu_ws._handle_event(_event("ou_owner", "再问一句半导体", mentions=["ou_bot"]))
    assert sent[0] == "第一答"
    assert any(k in sent[-1] for k in ("频繁", "稍后", "限频", "间隔"))


def test_local_chat_skips_llm_report_reclass(monkeypatch):
    """本地已判 chat 时不要再 LLM 二次判报告。「今天半导体怎么样」保持 chat。"""
    called = {"n": 0}

    class FakeLLM:
        def __init__(self, *a, **k):
            called["n"] += 1

        def run(self, **k):
            return "yes"

    monkeypatch.setattr("invest.agent.llm.LLMClient", FakeLLM)
    assert feishu_ws._is_report_request("今天半导体怎么样") is False
    assert called["n"] == 0

    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True)
    monkeypatch.setattr(feishu_ws, "_build_intraday_report",
        lambda public=False, brief=True: _report_struct("不应发报告"))
    monkeypatch.setattr("invest.agent.agents.run_chat",
        lambda conn, text, job="feishu_chat", chat_id="", skill="", **kw: "半导体板块分析")
    feishu_ws._handle_event(_event("ou_owner", "今天半导体怎么样", mentions=["ou_bot"]))
    assert sent == ["半导体板块分析"]
    assert all("不应发报告" not in (s or "") for s in sent)


def test_named_hangqing_and_report_aside_quote_at_gateway(monkeypatch):
    """网关：个股「现在行情」走会话报价；「盘中报告+顺便现价」仍走报告。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True)
    monkeypatch.setattr(feishu_ws, "_build_intraday_report",
        lambda public=False, brief=True: _report_struct("盘中报告正文"))
    chats = []

    def _fake_chat(conn, text, job="feishu_chat", chat_id="", skill="", **kw):
        chats.append(text)
        return "茅台现价 1400 [ev_1]"

    monkeypatch.setattr("invest.agent.agents.run_chat", _fake_chat)
    _patch_llm(monkeypatch, answer="yes")

    feishu_ws._handle_event(_event("ou_owner", "茅台现在行情怎么样", mentions=["ou_bot"]))
    assert chats == ["茅台现在行情怎么样"]
    assert any("1400" in (s or "") for s in sent)
    assert all("盘中报告正文" not in (s or "") for s in sent)

    sent.clear()
    chats.clear()
    feishu_ws._last_reply_at.clear()
    feishu_ws._handle_event(
        _event("ou_owner", "来一份盘中报告，顺便看下茅台现价", mentions=["ou_bot"]))
    assert chats == []
    assert any("盘中报告正文" in (s or "") for s in sent)


# ---------- 任务 4：盘中报告完整性门禁 / 限频五态 / 回执 ----------

def test_intraday_skips_full_report_when_data_insufficient(monkeypatch):
    """飞书盘中：数据不足时不发完整报告，只回短说明。"""
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
                        lambda cid, ctype, text: sent.append(text) or True)
    empty = ReportSnapshot(
        skill_id="b1_intraday",
        as_of="2026-08-28T10:30:00",
        blocks={
            "index_quotes": DataBlock(
                "index_quotes", "2026-08-28T10:30:00", True,
                payload=[], quotes=[],
            ),
            "core_quotes": DataBlock(
                "core_quotes", "2026-08-28T10:30:00", True,
                payload=[], quotes=[],
            ),
        },
    )
    monkeypatch.setattr("invest.skills.snapshot.freeze_snapshot", lambda *a, **k: empty)
    generated = []
    monkeypatch.setattr(
        "invest.skills.reports.b1_intraday.render",
        lambda *a, **k: generated.append(1) or {
            "title": "完整盘中",
            "sections": [{"type": "text", "text": "【盘面总览】不该发出的完整报告"}],
        },
    )
    _patch_llm(monkeypatch, answer="yes")
    feishu_ws._last_reply_at.clear()
    feishu_ws._handle_event(_event("ou_owner", "来一份盘中报告", mentions=["ou_bot"]))
    assert generated == []
    assert not any("不该发出的完整报告" in (s or "") for s in sent)
    assert any("数据不足" in (s or "") for s in sent)
    assert getattr(feishu_ws, "_last_report_result", None) is not None
    assert feishu_ws._last_report_result.status == "data_insufficient"


def test_intraday_rate_limit_writes_rate_limited(monkeypatch):
    """飞书盘中限频写 rate_limited，可观察。"""
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
                        lambda cid, ctype, text: sent.append(text) or True)
    monkeypatch.setattr(
        feishu_ws, "_build_intraday_report",
        lambda public=False, brief=True: _report_struct("完整报告"),
    )
    _patch_llm(monkeypatch, answer="yes")
    feishu_ws._last_reply_at.clear()
    feishu_ws._handle_event(_event("ou_owner", "盘中报告", mentions=["ou_bot"]))
    feishu_ws._handle_event(_event("ou_owner", "再来一份盘中报告", mentions=["ou_bot"]))
    assert any(k in sent[-1] for k in ("频繁", "稍后", "限频", "间隔"))
    assert feishu_ws._last_report_result.status == "rate_limited"


def test_intraday_send_writes_delivery_receipt(monkeypatch, tmp_path):
    """飞书盘中发送走任务1回执账本。"""
    from invest.data.quotes import QuoteResult, parse_asset
    from invest.db import connect, init_db
    from invest.skills.snapshot import DataBlock, ReportSnapshot

    db = str(tmp_path / "invest.db")
    init_db(db)
    monkeypatch.setattr(feishu_ws, "_invest_db", lambda: db)
    live = QuoteResult(
        ref=parse_asset("000001", "index"),
        price=3900.0, pct=0.003, status="live",
        freshness="unknown", fallback_level="none", src="tencent",
    )
    core = QuoteResult(
        ref=parse_asset("600519"),
        price=105.0, pct=0.05, status="live",
        freshness="unknown", fallback_level="none", src="sina",
    )
    ok_snap = ReportSnapshot(
        skill_id="b1_intraday",
        as_of="2026-08-28T10:31:00",
        blocks={
            "index_quotes": DataBlock(
                "index_quotes", "2026-08-28T10:31:00", True,
                payload=[live], quotes=[live],
            ),
            "core_quotes": DataBlock(
                "core_quotes", "2026-08-28T10:31:00", True,
                payload=[core], quotes=[core],
            ),
        },
    )
    monkeypatch.setattr("invest.skills.snapshot.freeze_snapshot", lambda *a, **k: ok_snap)
    monkeypatch.setattr(
        "invest.skills.reports.b1_intraday.render",
        lambda *a, **k: {
            "title": "盘中", "sections": [{"type": "text", "text": "ok报告"}],
            "as_of": ok_snap.as_of, "views": {},
        },
    )
    sent = []
    monkeypatch.setattr("invest.push.feishu_push.send_message",
                        lambda cid, ctype, text: sent.append(text) or True)
    _patch_llm(monkeypatch, answer="yes")
    feishu_ws._last_reply_at.clear()
    feishu_ws._handle_event(_event("ou_owner", "来一份盘中报告", mentions=["ou_bot"]))
    assert any("ok报告" in (s or "") for s in sent)
    conn = connect(db)
    try:
        rows = conn.execute(
            "SELECT channel, status FROM delivery_receipts WHERE message_id='b1_intraday'"
        ).fetchall()
    finally:
        conn.close()
    assert rows
    assert any(r["status"] == "succeeded" for r in rows)
    assert feishu_ws._last_report_result.status == "ok"


def test_intraday_receipt_distinguishes_chats_same_minute(monkeypatch, tmp_path):
    """同分钟不同 chat（群聊+私聊）不得因 succeeded 静默跳过第二路。"""
    import datetime as dt

    from invest.db import connect, init_db

    db = str(tmp_path / "invest.db")
    init_db(db)
    monkeypatch.setattr(feishu_ws, "_invest_db", lambda: db)
    monkeypatch.setattr(
        feishu_ws, "_build_intraday_report",
        lambda public=False, brief=True: _report_struct("ok报告"),
    )
    sent = []
    monkeypatch.setattr(
        "invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append((cid, text)) or True,
    )
    _patch_llm(monkeypatch, answer="yes")
    feishu_ws._last_reply_at.clear()

    frozen = dt.datetime(2026, 8, 28, 10, 31, 7)

    class _FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

        @classmethod
        def today(cls):
            return frozen.date()

    monkeypatch.setattr(dt, "datetime", _FrozenDateTime)
    monkeypatch.setattr(dt, "date", frozen.date().__class__)

    feishu_ws._handle_event(_event(
        "ou_owner", "来一份盘中报告", mentions=["ou_bot"],
        chat_id="oc_test", chat_type="group",
    ))
    feishu_ws._last_reply_at.clear()
    feishu_ws._handle_event(_event(
        "ou_owner", "来一份盘中报告", chat_id="oc_p2p", chat_type="p2p",
    ))

    bodies = [(cid, text) for cid, text in sent if text and "ok报告" in text]
    assert {cid for cid, _ in bodies} == {"oc_test", "oc_p2p"}, bodies
    conn = connect(db)
    try:
        rows = conn.execute(
            """SELECT run_slot, status FROM delivery_receipts
               WHERE message_id='b1_intraday' ORDER BY run_slot"""
        ).fetchall()
    finally:
        conn.close()
    slots = [r["run_slot"] for r in rows if r["status"] == "succeeded"]
    assert len(slots) == 2
    assert any("oc_test" in s or "oc_p2p" in s for s in slots)


def test_intraday_generate_exception_is_generate_failed(monkeypatch, tmp_path):
    """_build_intraday_report 异常应标 generate_failed，不得发出去后写成 ok。"""
    from invest.db import init_db

    db = str(tmp_path / "invest.db")
    init_db(db)
    monkeypatch.setattr(feishu_ws, "_invest_db", lambda: db)

    def _boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr("invest.skills.snapshot.freeze_snapshot", _boom)
    sent = []
    monkeypatch.setattr(
        "invest.push.feishu_push.send_message",
        lambda cid, ctype, text: sent.append(text) or True,
    )
    _patch_llm(monkeypatch, answer="yes")
    feishu_ws._last_reply_at.clear()
    feishu_ws._handle_event(_event("ou_owner", "来一份盘中报告", mentions=["ou_bot"]))
    assert feishu_ws._last_report_result is not None
    assert feishu_ws._last_report_result.status == "generate_failed"
    assert not any(r == "ok" for r in [feishu_ws._last_report_result.status])


# ---------- 长连接静默失活看门狗（2026-08-29：20:27 无响应事故）----------

def test_mark_frame_updates_timestamp():
    """任何 WS 帧到达都要刷新 _last_frame_at（看门狗判定依据）。"""
    import time

    feishu_ws._last_frame_at = 0.0
    feishu_ws._mark_frame()
    assert feishu_ws._last_frame_at > 0
    assert time.monotonic() - feishu_ws._last_frame_at < 1.0


def test_frame_stale_after_timeout():
    """超过 _NO_FRAME_TIMEOUT 无帧 → stale=True；无帧记录/刚收帧 → False。"""
    import time

    feishu_ws._last_frame_at = 0.0
    assert feishu_ws._frame_stale() is False  # 从未收帧：不判失活
    feishu_ws._last_frame_at = time.monotonic() - feishu_ws._NO_FRAME_TIMEOUT - 1.0
    assert feishu_ws._frame_stale() is True
    feishu_ws._mark_frame()
    assert feishu_ws._frame_stale() is False


def test_install_frame_hook_marks_every_frame():
    """钩子包装 _handle_message：原处理照常执行，且任何帧（含 PONG）都刷新时间戳。"""
    import asyncio

    feishu_ws._last_frame_at = 0.0

    class FakeCli:
        async def _handle_message(self, msg):
            self.seen = msg

    cli = FakeCli()
    assert feishu_ws._install_frame_hook(cli) is True
    asyncio.run(cli._handle_message(b"pong"))
    assert cli.seen == b"pong"
    assert feishu_ws._last_frame_at > 0


def test_install_frame_hook_failure_degrades_gracefully():
    """私有 API 缺失时钩子挂载失败返回 False，不抛异常（看门狗降级）。"""
    feishu_ws._last_frame_at = 0.0

    class NoHook:
        pass

    assert feishu_ws._install_frame_hook(NoHook()) is False
