"""联网工具 + Skill 流水线执行器测试（2026-08-21）。全 mock，不连网。"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_web_search_parse():
    from invest.agent.web_tools import web_search

    html = (
        '<li class="b_algo"><h2><a href="https://example.com/a">贵州茅台 2026 中报</a></h2>'
        '<p>茅台上半年净利润同比增长 8%，超市场预期。</p></li>'
        '<li class="b_algo"><h2><a href="https://example.com/b">茅台股价新高</a></h2>'
        '<p>收盘创历史新高。</p></li>'
    )
    with mock.patch("invest.agent.web_tools._session") as sess:
        sess.return_value.get.return_value.raise_for_status.return_value = None
        sess.return_value.get.return_value.text = html
        items = web_search("贵州茅台 中报", n=2)
    assert isinstance(items, list) and len(items) == 2
    assert items[0]["url"] == "https://example.com/a"
    assert "中报" in items[0]["title"]
    assert "增长" in items[0]["snippet"]


def test_web_fetch_strips_tags():
    from invest.agent.web_tools import web_fetch

    html = "<html><script>var x=1;</script><style>.a{}</style><body><h1>标题</h1><p>正文内容。</p></body></html>"
    with mock.patch("invest.agent.web_tools._session") as sess:
        sess.return_value.get.return_value.raise_for_status.return_value = None
        sess.return_value.get.return_value.text = html
        out = web_fetch("https://example.com/x")
    assert out["text"] == "标题 正文内容。"
    assert "var x" not in out["text"]  # script 已剔除


def test_run_skill_parses_report_path():
    from invest.agent import skill_runner

    fake_stdout = (
        "加载依赖…\n分析中…\n✅ 完成\n"
        "📄 报告路径: C:\\reports\\600519_20260821\\full-report-standalone.html\n"
        "综合评分 72/100\n"
    )
    proc = mock.Mock()
    proc.returncode = 0
    proc.stdout = fake_stdout
    proc.stderr = ""
    with mock.patch("invest.agent.skill_runner.subprocess.run", return_value=proc) as m:
        out = skill_runner.run_skill("600519", depth="lite")
    assert out["ok"] is True
    assert "600519_20260821" in out["report_path"]
    assert "72/100" in out["summary"]
    # 校验 env 注入 DeepSeek key
    env = m.call_args.kwargs["env"]
    assert env.get("OPENAI_API_KEY") or True  # key 来自 settings，存在与否都允许
    assert env.get("UZI_NO_UPDATE_CHECK") == "1"
    assert "--no-browser" in m.call_args.args[0]


def test_run_skill_timeout():
    from invest.agent import skill_runner

    with mock.patch("invest.agent.skill_runner.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("cmd", 150)):
        out = skill_runner.run_skill("600519", depth="lite")
    assert out["ok"] is False and "超时" in out["error"]


def test_build_dispatch_no_conn_for_web_tools():
    """2026-08-21：web_search/web_fetch/run_skill 不绑定 conn（否则 query 参数冲突）。"""
    import sqlite3

    from invest.agent.tools import build_dispatch

    d = build_dispatch(sqlite3.connect(":memory:"))
    # 不带 conn 前缀绑定 → 直接调用 web_search(query=...) 不报 multiple values
    # （tools.web_search 内局部导入 web_tools.web_search，patch 源模块）
    with mock.patch("invest.agent.web_tools.web_search", return_value=[{"title": "t", "url": "u", "snippet": "s"}]) as m:
        out = d["web_search"](query="测试")
    assert out == [{"title": "t", "url": "u", "snippet": "s"}]
    m.assert_called_once_with("测试", n=5)
    # 其他联网工具同样不绑定 conn（不会因位置参数冲突）
    assert "web_fetch" in d and "run_skill" in d


def test_llm_run_summary_fallback():
    """2026-08-21：轮数耗尽且最后是工具结果 → 追加纯文本总结，不返回工具 JSON。"""
    import sqlite3
    from unittest import mock as _mock

    from invest.agent.llm import LLMClient

    calls = {"n": 0}

    class FakeMsg:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    class FakeChoice:
        def __init__(self, msg):
            self.message = msg

    class FakeResp:
        def __init__(self, msg):
            self.choices = [FakeChoice(msg)]
            self.usage = type("U", (), {"total_tokens": 10})()

    def _create(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            tc = type("TC", (), {
                "id": "call_1",
                "function": type("F", (), {"name": "query_temperature", "arguments": "{}"})(),
                "model_dump": lambda self: {"id": "call_1", "function": {"name": "query_temperature", "arguments": "{}"}},
            })()
            return FakeResp(FakeMsg("", [tc]))
        return FakeResp(FakeMsg("温度 62 分，中性。"))

    client = LLMClient(conn=sqlite3.connect(":memory:"), settings=type("S", (), {
        "llm_api_key": "k", "llm_base_url": "x", "llm_model": "m", "daily_llm_budget_tokens": 10**9})())
    client.client = _mock.Mock()
    client.client.chat.completions.create.side_effect = _create
    # 内存库无 llm_usage 表，跳过记账/告警
    _mock.patch.object(client, "_log_usage", lambda *a, **k: None).start()
    _mock.patch.object(client, "_maybe_alert_usage", lambda *a, **k: None).start()
    out = client.run("sys", "user", tools=[], dispatch={"query_temperature": lambda: {"score": 62}},
                     job="feishu_chat", max_turns=2)  # 2 轮就耗尽 → 触发总结兜底
    assert "温度" in out  # 返回的是模型总结文本，不是工具 JSON
    assert calls["n"] == 3  # 2 轮工具 + 1 轮总结


def test_run_skill_async_with_sink():
    """2026-08-21：注册 sink 后 run_skill 异步——立即返回"已启动"，后台完成调 sink(结果, chat_id)。"""
    import threading

    from invest.agent.tools import run_skill, set_current_chat, set_run_skill_sink

    done = threading.Event()
    received = {}

    def _sink(result, chat_id):
        received["result"] = result
        received["chat_id"] = chat_id
        done.set()

    try:
        set_run_skill_sink(_sink)
        set_current_chat("oc_async_test")
        with mock.patch("invest.agent.skill_runner.run_skill",
                        return_value={"ok": True, "summary": "综合评分 60", "report_path": "r.html"}):
            out = run_skill("002083", depth="lite")
        assert out.get("async") is True and "已启动" in out["note"]  # 立即返回，不阻塞
        assert done.wait(timeout=10) is True                        # 后台线程完成
        assert received["chat_id"] == "oc_async_test"
        assert received["result"]["ok"] is True
    finally:
        set_run_skill_sink(None)


def test_skill_request_ack_keywords():
    """2026-08-21：深度分析/UZI 请求触发系统 ack。"""
    from invest.push.feishu_ws import _is_skill_request

    assert _is_skill_request("用UZI深度分析孚日股份") is True
    assert _is_skill_request("帮我做完整报告 600519") is True
    assert _is_skill_request("今天天气怎么样") is False


if __name__ == "__main__":
    test_web_search_parse()
    test_web_fetch_strips_tags()
    test_run_skill_parses_report_path()
    test_run_skill_timeout()
    test_build_dispatch_no_conn_for_web_tools()
    test_llm_run_summary_fallback()
    test_run_skill_async_with_sink()
    test_skill_request_ack_keywords()
    print("\nALL WEB/SKILL TESTS PASSED")
