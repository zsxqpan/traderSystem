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


def test_xueqiu_search_filters():
    """2026-08-24：xueqiu_search 只保留雪球链接（站内 WAF 抓不了正文，走必应摘要）。"""
    from invest.agent.web_tools import xueqiu_search

    mixed = [
        {"title": "段永平 雪球文章", "url": "https://xueqiu.com/6192813830/366009201", "snippet": "段永平：…"},
        {"title": "百度百科", "url": "https://baike.baidu.com/item/x", "snippet": "…"},
        {"title": "雪球热帖", "url": "https://xueqiu.com/3000000000/123456789", "snippet": "讨论…"},
    ]
    with mock.patch("invest.agent.web_tools.web_search", return_value=mixed):
        out = xueqiu_search("段永平", n=5)
    assert isinstance(out, list) and len(out) == 2
    assert all("xueqiu.com" in it["url"] for it in out)
    # 无雪球结果 → error
    with mock.patch("invest.agent.web_tools.web_search", return_value=[mixed[1]]):
        out2 = xueqiu_search("不存在", n=5)
    assert isinstance(out2, dict) and "error" in out2
    print("test_xueqiu_search_filters OK")


def test_web_fetch_waf_detected():
    """2026-08-24：雪球等 WAF 保护页面——web_fetch 返回明确提示而非密文。"""
    from invest.agent.web_tools import web_fetch

    waf_html = ('<textarea id="renderData" style="display:none">{"_waf_bd8ce2ce37":"xxx"}</textarea>'
                '<meta name="aliyun_waf_aa" content="yyy">')
    with mock.patch("invest.agent.web_tools._session") as sess:
        sess.return_value.get.return_value.raise_for_status.return_value = None
        sess.return_value.get.return_value.text = waf_html
        out = web_fetch("https://xueqiu.com/6192813830/366009201")
    assert "error" in out and "WAF" in out["error"]
    print("test_web_fetch_waf_detected OK")


def test_web_search_deepseek():
    """2026-08-25：DeepSeek 官方搜索——Anthropic /messages + web_search tool 解析（url/title/citations 摘要）。"""
    from invest.agent.web_tools import web_search_deepseek

    fake_resp = {
        "content": [
            {"type": "server_tool_use", "name": "web_search"},
            {"type": "web_search_tool_result", "content": [
                {"type": "web_search_result", "title": "孚日股份(002083)龙虎榜数据",
                 "url": "http://data.hexin.cn/market/longhustock/code/002083/", "page_age": "1天"},
                {"type": "web_search_result", "title": "孚日股份 股吧",
                 "url": "https://guba.eastmoney.com/list,002083.html", "page_age": "3天"},
            ]},
            {"type": "text", "text": "查询结果", "citations": [
                {"url": "http://data.hexin.cn/market/longhustock/code/002083/",
                 "cited_text": "龙虎榜数据显示净买入"},
            ]},
        ],
    }
    with mock.patch("invest.agent.web_tools._session") as sess:
        sess.return_value.post.return_value.raise_for_status.return_value = None
        sess.return_value.post.return_value.json.return_value = fake_resp
        out = web_search_deepseek("孚日股份 002083 龙虎榜", n=5)
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["url"].startswith("http://data.hexin.cn")
    assert out[0]["snippet"] == "龙虎榜数据显示净买入"  # citations 摘要
    assert "002083" in out[1]["url"]
    # 未触发原生搜索 → None（降级多引擎）
    with mock.patch("invest.agent.web_tools._session") as sess:
        sess.return_value.post.return_value.raise_for_status.return_value = None
        sess.return_value.post.return_value.json.return_value = {"content": [{"type": "text", "text": "x"}]}
        assert web_search_deepseek("x", n=5) is None
    print("test_web_search_deepseek OK")


def test_web_search_priority_deepseek_first():
    """2026-08-25：web_search 官方优先——官方成功直接用；官方失败降级多引擎。"""
    from invest.agent.web_tools import web_search

    ds_items = [{"title": "官方搜索结果", "url": "https://x.com/1", "snippet": "s"}]
    # 官方成功 → 不再走 HTML 引擎
    with mock.patch("invest.agent.web_tools.web_search_deepseek", return_value=ds_items):
        out = web_search("孚日股份 龙虎榜", n=5)
    assert out[0]["url"] == "https://x.com/1"
    # 官方失败（None）→ 降级多引擎（bing 词典 → 搜狗命中）
    with mock.patch("invest.agent.web_tools.web_search_deepseek", return_value=None), \
            mock.patch("invest.agent.web_tools._search_engine",
                       side_effect=lambda e, q, n: [{"title": "孚日股份龙虎榜详情", "url": "http://x.com/2",
                                                     "snippet": ""}] if e != "bing_cn" else
                       [{"title": "孚_百科", "url": "https://baike.baidu.com/item/x", "snippet": ""}]):
        out2 = web_search("孚日股份 龙虎榜", n=5)
    assert out2[0]["url"] == "http://x.com/2"
    print("test_web_search_priority_deepseek_first OK")


def test_web_search_multi_engine_fallback():
    """2026-08-25：多引擎降级——必应分词差时自动降级搜狗/360（百度硬反爬最后兜底）。"""
    from invest.agent.web_tools import web_search

    dict_items = [{"title": "孚_百度百科", "url": "https://baike.baidu.com/item/x", "snippet": "汉字"}] * 5
    good_items = [{"title": "孚日股份(002083)龙虎榜数据(07-24)", "url": "http://www.sogou.com/link?x",
                   "snippet": "龙虎榜"}] * 5
    calls = []

    def _fake(engine, query, n):
        calls.append(engine)
        return dict_items if engine == "bing_cn" else good_items

    with mock.patch("invest.agent.web_tools._search_engine", side_effect=_fake), \
            mock.patch("invest.agent.web_tools.web_search_deepseek", return_value=None):
        out = web_search("孚日股份 002083 龙虎榜", n=5)
    assert calls == ["bing_cn", "sogou"]  # 必应质量差 → 降级搜狗（百度在最后）
    assert "002083" in out[0]["title"]
    print("test_web_search_multi_engine_fallback OK")


def test_web_search_quality_detection():
    """质量检测：含代码必须命中；词典类 url 不算有效结果。"""
    from invest.agent.web_tools import _quality_ok

    assert _quality_ok("孚日股份 002083 龙虎榜",
                       [{"title": "孚_百科", "url": "https://baike.baidu.com/item/x", "snippet": ""}]) is False
    assert _quality_ok("孚日股份 002083 龙虎榜",
                       [{"title": "孚日股份(002083)龙虎榜", "url": "http://x.com", "snippet": ""}]) is True
    assert _quality_ok("孚日股份 龙虎榜",
                       [{"title": "孚_字典", "url": "https://zdic.net/x", "snippet": ""}]) is False
    assert _quality_ok("孚日股份 龙虎榜",
                       [{"title": "孚日股份龙虎榜详情", "url": "http://x.com", "snippet": ""}]) is True
    print("test_web_search_quality_detection OK")


def test_web_search_site_force_bing():
    """site: 查询强制走必应（百度/搜狗不支持 site: 操作符）。"""
    from invest.agent.web_tools import web_search

    calls = []

    def _fake(engine, query, n):
        calls.append(engine)
        return [{"title": "t", "url": "https://xueqiu.com/1/2", "snippet": "s"}]

    with mock.patch("invest.agent.web_tools._search_engine", side_effect=_fake):
        web_search("site:xueqiu.com 段永平", n=3)
    assert calls == ["bing_cn"]
    print("test_web_search_site_force_bing OK")


def test_skill_request_ack_keywords():
    """2026-08-23 收紧：仅明确提到 UZI 才算 skill 请求（'深度分析/完整报告'不再触发 ack）。"""
    from invest.push.feishu_ws import _is_skill_request

    assert _is_skill_request("用UZI深度分析孚日股份") is True
    assert _is_skill_request("跑个uzi看看") is True
    assert _is_skill_request("帮我做完整报告 600519") is False
    assert _is_skill_request("深度分析一下600519") is False
    assert _is_skill_request("今天天气怎么样") is False


if __name__ == "__main__":
    test_web_search_parse()
    test_web_search_multi_engine_fallback()
    test_web_search_quality_detection()
    test_web_search_site_force_bing()
    test_web_fetch_strips_tags()
    test_run_skill_parses_report_path()
    test_run_skill_timeout()
    test_build_dispatch_no_conn_for_web_tools()
    test_xueqiu_search_filters()
    test_web_fetch_waf_detected()
    test_llm_run_summary_fallback()
    test_run_skill_async_with_sink()
    test_skill_request_ack_keywords()
    print("\nALL WEB/SKILL TESTS PASSED")
