"""飞书长连接接收器（项目本体直连，lark-oapi WebSocket，零 Hermes 依赖）。

替代旧方案（Hermes 桌面端 gateway.log 轮询 / Hermes 飞书连接）。功能（2026-08-18 v4）：
1) **私聊（p2p）**：任何消息都由 Agent 回应（报告/提问/闲聊），非管理员计入每日限额；
2) **群内 @机器人**（管理员或其他人 @）：任意消息都由 Agent 回应，不再只回报告；
3) 管理员群内不 @ 的发言：仅识别为要实时报告才触发（2026-08-21 起两级意图识别：
   关键词快速判定优先、未命中再走 LLM 语义兜底，绝大多数报告请求零 token）；
4) 非管理员（群内 @ 或私聊）每日 token 限额 `FEISHU_NONADMIN_DAILY_TOKEN_LIMIT`（默认 100 万，
   记入 llm_usage job='group'），超限只回额度提示、不再消耗 token；
5) 机器人自己的消息 → 忽略。

回复分流（_agent_reply）：
- 识别为要实时报告 → 盘中实时报告（非管理员=公开版，无持仓警戒）；LLM 失败/超时 → 不生成；
  **意图识别（2026-08-21）**：群聊=关键词优先+LLM 兜底；私聊 p2p=始终 LLM 语义判断（不走关键词）；
- 问候/求助（在吗/你好/help…，本地判定零 token）→ 帮助提示；
- 其他 → 会话 Agent（run_chat，带系统数据工具）回答，max_turns=3 控制成本；
  **按需求路由 Skill**（产业链/基本面→Serenity；短线/异动/游资→youzi；五步法→stock_analysis），
  并在回复末尾用斜体行标注「↘ 已使用 Skill：xxx」（飞书消息不支持小字号，用斜体弱化近似）。

2026-08-18 其他：
- 收到用户消息先给该消息加 ❤️ 表情回应（im:message.reaction 权限），告知已收到；
- 限频：报告 30s/人，Agent 会话 10s/人。

前置条件（开发者后台 open.feishu.cn/app，应用即群内机器人 Trader-Fox）：
- 「事件与回调 → 事件订阅」订阅方式选：使用长连接接收事件；
- 订阅事件：接收消息 im.message.receive_v1（v2.0）；
- 权限：im:message、im:message.group_at_msg、im:message.p2p_msg 等；
- 应用机器人已加入目标群（FEISHU_CHAT_ID）；
- 注意：飞书长连接是集群模式，同一应用同一时刻只有随机一个客户端能收到事件。
  启用本项目连接前，必须在 Hermes 桌面端停用该应用的飞书连接（或卸载应用），
  否则消息会在多个客户端间随机分流，表现为“艾特机器人经常没回应”。

依赖：lark-oapi>=1.7.2（requirements.txt 已声明）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from invest.config import get_settings

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
LOG_FILE = ROOT / "logs" / "feishu_ws.log"
_log_handler_attached = False


def setup_file_logging() -> None:
    """把 lark_oapi 与本模块日志写入 logs/feishu_ws.log（pythonw 无控制台时也能排查）。

    幂等：只挂一次。只挂 root（子 logger 经 propagation 复用同一 handler，避免重复写）。
    """
    global _log_handler_attached
    if _log_handler_attached:
        return
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        if not any(isinstance(x, logging.FileHandler) for x in root.handlers):
            h = logging.FileHandler(LOG_FILE, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(h)
        root.setLevel(logging.INFO)
        # apscheduler 每 4 秒一条 INFO 会刷爆文件，降到 WARNING
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        _log_handler_attached = True
    except Exception as exc:
        print(f"[feishu_ws] 文件日志初始化失败: {exc}")

MAX_REPORT_LEN = 3800
REPLY_MIN_INTERVAL = 120.0  # 同一发送者两次报告回复的最小间隔（秒）；2026-08-22 由 30s 提到 120s（报告含 LLM）
CHAT_MIN_INTERVAL = 10.0   # 同一发送者两次 Agent 会话回复的最小间隔（秒）
_CHAT_MAX_LEN = 6000       # Agent 会话回复最大长度（2026-08-27 由 2000 提：全能 Agent 分析可展开）

_GREETING_KEYWORDS = ("在吗", "在不在", "你好", "您好", "hello", "hi", "help", "帮助", "你是谁", "你会什么", "怎么用")

# 意图判定第一级负向词（2026-08-21）：明确非"当前实时报告"意图 → 直接 no，省一次 LLM
_REPORT_NEG = (
    "历史", "上周", "上月", "上个月", "去年", "周报", "月报", "年报", "季度报",
    "回测", "模拟", "新闻", "政策", "财报", "公告", "研报", "复盘总结",
)

_HELP_TEXT = (
    "在的。可以这样用我：\n"
    "· @我并说「来一份盘中报告 / 现在行情」→ 盘中实时报告（板块异动/龙头人气/核心池行情）；\n"
    "· 直接问我市场问题（如'今天哪些板块最强''怎么看军工'）→ 我会查系统数据回答；\n"
    "· 私聊我也一样。非管理员每天有 token 额度上限（100万）。"
)

_bot_open_id_cache: str = ""
_last_reply_at: dict[str, float] = {}
_last_skill_ack = 0.0


def _bot_open_id() -> str:
    """机器人自己的 open_id：优先取配置，未配置则查 bot/v3/info（只查一次）。"""
    global _bot_open_id_cache
    if _bot_open_id_cache:
        return _bot_open_id_cache
    settings = get_settings()
    if getattr(settings, "feishu_bot_open_id", ""):
        _bot_open_id_cache = settings.feishu_bot_open_id
        return _bot_open_id_cache
    try:
        from invest.push.feishu_push import _tenant_token

        token = _tenant_token()
        if not token:
            return ""
        import requests

        s = requests.Session()
        s.trust_env = False
        r = s.get(
            "https://open.feishu.cn/open-apis/bot/v3/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        d = r.json()
        if d.get("code") == 0:
            _bot_open_id_cache = d["bot"]["open_id"]
    except Exception as exc:
        logger.warning("获取机器人 open_id 失败: %s", exc)
    return _bot_open_id_cache


def _extract_text(content: str | None, msg_type: str | None) -> str:
    """提取消息纯文本：支持 text 与 post 两种类型。

    - text: content 是 JSON 字符串 {"text": "..."}；
    - post: content 是 JSON 字符串 {"title": "...", "content": [[{"tag":"text","text":"..."}...]]}
      （部分客户端 @ 机器人时发的是富文本 post，2026-08-18 兼容）。
    """
    if not content:
        return ""
    try:
        d = json.loads(content)
    except Exception:
        return content
    if msg_type == "text":
        return d.get("text", "") if isinstance(d, dict) else ""
    if msg_type == "post":
        parts: list[str] = []
        blocks = d.get("content") or [] if isinstance(d, dict) else []
        for block in blocks:
            for seg in block or []:
                if isinstance(seg, dict) and seg.get("tag") == "text":
                    parts.append(seg.get("text", ""))
        return "".join(parts)
    return ""


def _mention_ids(m) -> list[str]:
    """提取 Mention 的所有 id，兼容三种形态（实测为第 3 种）：

    1. str：Mention.id 直接是 open_id 字符串（lark 文档形态）；
    2. dict：{"open_id": ..., "user_id": ..., "union_id": ...}；
    3. UserId 对象：lark-oapi 的 MentionEvent.id 是 UserId 实例（带 .open_id/.user_id/.union_id 属性）。
    """
    mid = getattr(m, "id", None)
    if isinstance(mid, str):
        return [mid]
    if isinstance(mid, dict):
        return [str(v) for v in (mid.get("open_id"), mid.get("user_id"), mid.get("union_id")) if v]
    if mid is not None:  # UserId 对象形态
        return [str(v) for v in (
            getattr(mid, "open_id", None),
            getattr(mid, "user_id", None),
            getattr(mid, "union_id", None),
        ) if v]
    return []


def _is_mentioned(msg) -> bool:
    """消息是否 @ 了机器人（2026-08-18 修复：Mention.id 是 dict 而非 str）。"""
    bot_id = _bot_open_id()
    if not bot_id:
        return False
    mentions = getattr(msg, "mentions", None) or []
    for m in mentions:
        if bot_id in _mention_ids(m):
            return True
    return False


def _keyword_report_request(text: str) -> bool | None:
    """意图判定**第一级：关键词快速判定**（2026-08-21 新增，零 token）。

    返回 True=要实时报告 / False=明确不要 / None=未决（交 LLM 兜底）。
    - 负向词先查（历史/周报/新闻等明确非"当前实时报告"意图 → False，省一次 LLM）；
    - 正向=「名词(报告/行情/盘面/盘口/异动) + 限定(现在/今天/实时/来一份…)」组合 → True；
    - 含 6 位股票代码且不含"报告"的文本（如"600519 现在行情怎么样"）→ None 交 LLM，
      避免把个股查询误判成盘中报告。
    """
    import re

    t = (text or "").strip()
    if not t:
        return False
    if any(k in t for k in _REPORT_NEG):
        return False
    # 纯"报告"类极短消息（"报告" / "来一份报告"）
    if len(t) <= 12 and t in ("报告", "来报告", "发报告", "来份报告", "来一份报告", "实时报告"):
        return True
    has_noun = any(k in t for k in ("报告", "行情", "盘面", "盘口", "异动"))
    has_qual = any(k in t for k in ("盘中", "现在", "今天", "当前", "目前", "实时",
                                    "来一份", "来份", "发我", "发个", "发份", "看看", "怎么样", "如何"))
    if has_noun and has_qual:
        has_code = re.search(r"\d{6}", t) is not None
        if has_code and "报告" not in t:
            return None  # 个股查询（"600519 现在行情"），交 LLM 语义判断
        return True
    return None


def _is_report_request(text: str, track_job: str | None = None, keyword: bool = True) -> bool:
    """意图判定：**两级识别**（2026-08-21 由纯 LLM 改为关键词优先 + LLM 兜底）。

    - 第一级 `_keyword_report_request`：零 token 快速判定，命中即返回（绝大多数
      "来一份盘中报告/现在行情怎么样" 类请求不再消耗 LLM）；
    - 未决（None）才走 LLM 语义识别（保留对多样表达的语义能力）；
    - keyword=False（**私聊 p2p，2026-08-21**）：跳过关键词第一级，始终 LLM 语义判断
      （私聊对话上下文更随意，避免关键词误判/漏判）；
    - 负向词/LLM 说 no → False；LLM 失败/超时/无 key → False
      （此时 @ 场景仍会回帮助提示，不会完全静默）；
    - track_job 非空（非管理员）：LLM 兜底调用带 conn，用量记入 llm_usage(job=track_job)，
      供每日 token 限额核算。
    """
    if keyword:
        kw = _keyword_report_request(text)
        if kw is not None:
            return kw
    try:
        from invest.agent.llm import LLMClient

        settings = get_settings()
        if not settings.llm_api_key:
            return False
        conn = None
        if track_job:
            from invest.db import connect

            conn = connect(str(ROOT / "data" / "invest.db"))
        try:
            client = LLMClient(conn=conn, settings=settings)
            sys_prompt = (
                "你是意图分类器。判断用户是否在请求「盘中实时报告/当前行情快照」，只输出一个词：\n"
                "yes = 明确要求看当前/实时行情报告、盘中异动、今日盘面（如'来一份盘中报告''现在行情怎么样''发个实时报告'）\n"
                "no = 其他一切（闲聊、问历史数据、问个股基本面、讨论策略、要求定时报告等）\n"
                "注意：只有明确要『当前实时』行情/报告才算 yes。"
            )
            out = client.run(system=sys_prompt, user=text[:300],
                             job=track_job or "intent", max_turns=1)
            return "yes" in (out or "").strip().lower()
        finally:
            if conn is not None:
                conn.close()
    except Exception as exc:
        logger.warning("意图判定 LLM 失败: %s", exc)
        return False


def _nonadmin_budget_exceeded() -> bool:
    """非管理员每日 token 限额检查（llm_usage job='group' 当日累计 >= 限额）。"""
    try:
        from invest.config import get_settings as _gs

        limit = getattr(_gs(), "feishu_nonadmin_daily_token_limit", 1_000_000) or 0
        if limit <= 0:
            return False
        from invest.db import connect

        conn = connect(str(ROOT / "data" / "invest.db"))
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(tokens),0) AS t FROM llm_usage "
                "WHERE job='group' AND date=date('now','localtime')"
            ).fetchone()
            return (row["t"] or 0) >= limit
        finally:
            conn.close()
    except Exception:
        return False


def _build_intraday_report(public: bool = False, brief: bool = True) -> dict:
    """生成盘中实时报告结构（2026-08-22：经 Skill Runner 调 b1_intraday，结构化输出）。

    失败返回含错误文本的节（调用方按卡片/纯文本统一发送）。
    """
    try:
        from invest.skills.runner import run_structured

        db = str(ROOT / "data" / "invest.db")
        return run_structured("b1_intraday", db_path=db, public=public, brief=brief)
    except Exception as exc:
        return {"sections": [{"type": "text", "text": f"[报告生成失败: {type(exc).__name__}: {exc}]"}]}


def _persist_intraday_views(views: dict) -> None:
    """盘中报告观点落库（2026-08-22）：source='intraday_report'，供盘后日报点2 复盘。失败静默。"""
    if not views:
        return
    try:
        import json

        from invest.db import connect as _connect

        conn = _connect(str(ROOT / "data" / "invest.db"))
        try:
            for kind in ("mood", "mainline"):
                content = views.get(kind)
                if not content:
                    continue
                conn.execute(
                    """INSERT INTO viewpoints(source, conclusion, period_tag, confidence,
                       evidence_json, invalid_condition, status, created_at, obj_type, obj)
                       VALUES('intraday_report', ?, 'micro', 0.5, '[]', '当日收盘复盘', 'active',
                              datetime('now','localtime'), 'market', ?)""",
                    (json.dumps(content, ensure_ascii=False)[:2000], kind),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("盘中观点落库失败: %s", exc)


def _send_report(chat_id: str, struct: dict) -> bool:
    """发送结构化报告：飞书卡片（表格/图表/加粗）优先，失败回退纯文本。"""
    from invest.push.feishu_push import send_card, send_message
    from invest.push.render import render_feishu, render_plain

    card = render_feishu(struct)
    if card.get("body", {}).get("elements") and send_card(chat_id, "chat_id", card):
        return True
    plain = render_plain(struct)
    if len(plain) > MAX_REPORT_LEN:
        plain = plain[:MAX_REPORT_LEN] + "\n…(截断)"
    return send_message(chat_id, "chat_id", plain)


def _is_greeting(text: str) -> bool:
    """问候/求助类短消息 → 直接回帮助提示（本地判定，不耗 LLM token）。"""
    t = text.strip().lower()
    return len(t) <= 20 and any(k in t for k in _GREETING_KEYWORDS)


def _agent_chat(text: str, nonadmin: bool = False, chat_id: str = "") -> str:
    """会话 Agent 回复（2026-08-18：Skill 由大模型语义自选并在回复中自标注）。

    2026-08-24：chat_id 传入 run_chat，启用多轮对话记忆（读/写 chat_history）。
    非管理员计入 job='group' 限额。
    """
    try:
        from invest.agent.agents import run_chat
        from invest.db import connect

        conn = connect(str(ROOT / "data" / "invest.db"))
        try:
            # 2026-08-23：记录用户原文（thread-local），run_skill 门禁据此判断是否明确提到 UZI；
            # 并启用数据新鲜度硬门禁——数据滞后时数据工具返回原因而非旧数据（对话结束复位）
            from invest.agent.tools import set_current_user_text, set_freshness_gate

            set_current_user_text(text[:1000])
            set_freshness_gate(True)
            try:
                out = run_chat(conn, text[:1000], job="group" if nonadmin else "feishu_chat",
                               chat_id=chat_id)
            finally:
                set_freshness_gate(False)
                set_current_user_text("")  # 复位，避免 thread-local 残留影响后续（含测试）
        finally:
            conn.close()
        out = (out or "").strip()
        if not out:
            return "（Agent 无输出）"
        if out.startswith("[预算不足"):
            return out
        if len(out) > _CHAT_MAX_LEN:
            out = out[:_CHAT_MAX_LEN] + "\n…(截断)"
        return out
    except Exception as exc:
        logger.warning("Agent 回复失败: %s", exc)
        return f"[Agent 回复失败: {type(exc).__name__}: {exc}]"


def _agent_reply(chat_id: str, text: str, sender_id: str, nonadmin: bool = False,
                 chat_type: str = "group") -> None:
    """统一回复入口（2026-08-18）：私聊 / 群内 @ 的任意消息。

    分流：识别为要实时报告 → 盘中实时报告（非管理员=公开版，不计 Agent token）；
          问候/求助 → 帮助提示（本地判定，零 token）；
          其他 → 会话 Agent（run_chat）回答。
    意图识别（2026-08-21）：群聊（group）关键词优先 + LLM 兜底；私聊（p2p）始终 LLM 语义判断。
    非管理员：任何 LLM 调用都计入 job='group'，受 FEISHU_NONADMIN_DAILY_TOKEN_LIMIT（100万/日）约束。
    """
    from invest.push.feishu_push import send_message

    if nonadmin and _nonadmin_budget_exceeded():
        send_message(chat_id, "chat_id",
                     "今日非管理员 token 额度（100万）已用完，请明天再试。")
        return

    # 2026-08-21：UZI 深度分析请求先系统级 ack（异步跑，约 5-20 分钟，防止长时间静默）
    if _is_skill_request(text) and _skill_ack_ok():
        send_message(chat_id, "chat_id",
                     "⏳ 收到，正在用 UZI 深度分析，约 5-20 分钟（完成后自动发送报告摘要与路径）。")

    want_report = _is_report_request(
        text, track_job="group" if nonadmin else None, keyword=chat_type != "p2p")
    if want_report:
        if _rate_limited(sender_id):
            logger.info("报告请求限频跳过（120s 内重复）: %s", text[:30])
            return
        # 2026-08-22：默认完整版；说"简洁/简短/精简"才发简洁版（只留客观盘面）
        brief = any(k in text for k in ("简洁", "简短", "精简", "简版", "简略"))
        logger.info("盘中报告请求（nonadmin=%s brief=%s）: %s", nonadmin, brief, text[:40])
        send_message(chat_id, "chat_id", "⏳ 收到，正在生成盘中实时报告…")
        report = _build_intraday_report(public=nonadmin, brief=brief)
        ok = _send_report(chat_id, report)
        # 2026-08-22：盘中观点落库（source='intraday_report'），供盘后日报点2 复盘
        _persist_intraday_views(report.get("views") or {})
        logger.info("盘中报告回复完成 ok=%s", ok)
        return

    if _is_greeting(text):
        send_message(chat_id, "chat_id", _HELP_TEXT)
        return

    now = time.time()
    last = _last_reply_at.get("chat:" + sender_id, 0.0)
    if now - last < CHAT_MIN_INTERVAL:
        logger.info("Agent 会话限频跳过（%ss 内重复）: %s", int(CHAT_MIN_INTERVAL), text[:30])
        return
    _last_reply_at["chat:" + sender_id] = now

    logger.info("Agent 会话回复（nonadmin=%s）: %s", nonadmin, text[:40])
    reply = _agent_chat(text, nonadmin=nonadmin, chat_id=chat_id)
    # Skill 由大模型语义自选并在回复文本末尾自标注（"↘ 已使用 Skill：xxx"），原样发送
    ok = send_message(chat_id, "chat_id", reply)
    logger.info("Agent 回复完成 ok=%s len=%d", ok, len(reply))


def _is_skill_request(text: str) -> bool:
    """是否请求 UZI 深度分析（2026-08-23 收紧：**仅明确提到 UZI 才算**；
    '深度分析/完整报告'等词不再触发 ack——由 Agent 按角度 skill 处理）。"""
    t = (text or "").lower()
    return "uzi" in t


def _skill_ack_ok() -> bool:
    """UZI ack 限频：60s 内不重复发。"""
    global _last_skill_ack
    now = time.time()
    if now - _last_skill_ack < 60.0:
        return False
    _last_skill_ack = now
    return True


def _rate_limited(sender_id: str) -> bool:
    """同一发送者限频：REPLY_MIN_INTERVAL 内不重复回复。"""
    now = time.time()
    last = _last_reply_at.get(sender_id, 0.0)
    if now - last < REPLY_MIN_INTERVAL:
        return True
    _last_reply_at[sender_id] = now
    return False


def _handle_event(data: P2ImMessageReceiveV1) -> None:
    """事件路由（在独立线程中执行，避免阻塞 SDK 事件循环）。"""
    settings = get_settings()
    target_chat = settings.feishu_chat_id
    owner_open_id = settings.feishu_owner_open_id
    if not target_chat or not owner_open_id:
        logger.warning("未配置 FEISHU_CHAT_ID / FEISHU_OWNER_OPEN_ID，跳过事件")
        return

    event = data.event
    if event is None:
        return
    msg = event.message
    if msg is None:
        return
    chat_id = msg.chat_id or ""
    text = _extract_text(msg.content, msg.message_type)
    # 记录当前会话 id（thread-local），供 run_skill 异步完成回调发回原会话
    try:
        from invest.agent.tools import set_current_chat

        set_current_chat(chat_id)
    except Exception:
        pass
    # 所有收到的事件都落日志（含非目标群），便于排查"艾特没回应"
    logger.info(
        "收到事件 chat=%s chat_type=%s type=%s mentioned=%s mentions=%r text=%r",
        chat_id, getattr(msg, "chat_type", ""), msg.message_type, _is_mentioned(msg),
        getattr(msg, "mentions", None), text[:60],
    )

    sender = event.sender
    sender_id = (sender.sender_id.open_id if sender and sender.sender_id else "") or ""
    sender_type = sender.sender_type if sender else ""

    if not text.strip():
        return

    if sender_type == "bot" or (sender_id and sender_id == _bot_open_id()):
        return  # 机器人自己发言，不处理

    # ---- 私聊（p2p）：任何消息都回应（非管理员计入每日限额）----
    # 意图识别：私聊始终走 LLM 语义判断（keyword=False），不用群聊的关键词短路
    if getattr(msg, "chat_type", "") == "p2p":
        _agent_reply(chat_id, text, sender_id, nonadmin=sender_id != owner_open_id,
                     chat_type="p2p")
        return

    if chat_id != target_chat:
        return  # 只处理目标群

    mentioned = _is_mentioned(msg)

    if sender_id == owner_open_id:
        # ---- 管理员 ----
        # 2026-08-25：群聊**仅在被 @ 机器人时**回应；未 @ 一律静默——
        # 移除旧的"未@ 语义识别为报告请求仍触发盘中报告"分支（正常聊天含'盘中'等词会被误触发）
        if mentioned:
            # 群内 @ 管理员：任意消息都由 Agent 回应（报告/闲聊/提问）
            _agent_reply(chat_id, text, sender_id, nonadmin=False)
        return  # 未 @ 的管理员发言一律不回复（含报告请求）

    # ---- 非管理员（限额 100 万/日，超限只回额度提示）----
    if mentioned:
        _agent_reply(chat_id, text, sender_id, nonadmin=True)
    # 未 @ 的非管理员消息：忽略（不打扰群聊）


def _format_skill_result(result: dict) -> str:
    """run_skill 异步完成结果 → 飞书消息。"""
    if result.get("ok"):
        lines = ["✅ UZI 深度分析完成"]
        summary = (result.get("summary") or "").strip()
        if summary:
            lines.append(summary)
        path = result.get("report_path")
        if path:
            lines.append(f"📄 报告路径: {path}")
        return "\n".join(lines)
    err = result.get("error") or "未知错误"
    return f"❌ UZI 深度分析失败：{str(err)[:500]}"


def _register_run_skill_sink() -> None:
    """注册 run_skill 异步完成回调：把结果发回原会话（私聊/群聊均支持）。"""
    try:
        from invest.agent.tools import set_run_skill_sink
        from invest.push.feishu_push import send_message as _send

        def _sink(result: dict, chat_id: str) -> None:
            if not chat_id:
                return
            _send(chat_id, "chat_id", _format_skill_result(result))
            logger.info("run_skill 异步结果已发送 chat=%s ok=%s", chat_id, result.get("ok"))

        set_run_skill_sink(_sink)
    except Exception as exc:
        logger.warning("注册 run_skill sink 失败: %s", exc)


def _should_react(msg) -> bool:
    """是否给消息回 ❤️（2026-08-20：仅当艾特了机器人，或私聊 p2p 才回；普通群聊消息不回）。"""
    if getattr(msg, "chat_type", "") == "p2p":
        return True
    return _is_mentioned(msg)


def _handle_event_async(data: P2ImMessageReceiveV1) -> None:
    """包装：事件处理放入守护线程，不阻塞 SDK 的事件循环（报告生成较耗时）。

    线程内异常必须落日志（pythonw 无控制台，异常默认不可见——2026-08-18 排查修复）。
    2026-08-18：处理前先给消息加 ❤️ 表情回应（告知已收到；失败静默），需权限 im:message.reaction。
    2026-08-20：仅艾特机器人/私聊的消息回爱心，普通群聊消息不回。
    """

    def _wrapper() -> None:
        try:
            msg = getattr(getattr(data, "event", None), "message", None)
            if msg is not None and getattr(msg, "message_id", None) and _should_react(msg):
                try:
                    from invest.push.feishu_push import add_reaction

                    add_reaction(msg.message_id, "HEART")
                except Exception as exc:
                    logger.warning("消息表情回应失败: %s", exc)
            _handle_event(data)
        except Exception:
            logger.exception("事件处理异常")

    threading.Thread(target=_wrapper, daemon=True).start()


def _ignore_event(_data) -> None:
    """忽略未订阅业务的事件（如表情回应 created/deleted），避免日志 ERROR 噪音。"""


def check() -> None:
    """连通性自检：凭据、机器人信息、长连接端点（不发消息、不建连接）。"""
    settings = get_settings()
    from invest.push.feishu_push import _tenant_token

    token = _tenant_token()
    print(f"app_id       = {settings.feishu_app_id}")
    print(f"chat_id      = {settings.feishu_chat_id}")
    print(f"owner_open_id= {settings.feishu_owner_open_id}")
    print(f"tenant_token = {'OK' if token else 'FAIL'}")
    if not token:
        return
    import requests

    s = requests.Session()
    s.trust_env = False
    r = s.get(
        "https://open.feishu.cn/open-apis/bot/v3/info",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    d = r.json()
    if d.get("code") == 0:
        print(f"bot          = {d['bot'].get('app_name')} open_id={d['bot'].get('open_id')}")
    else:
        print(f"bot info FAIL: {d}")
    r = s.post(
        "https://open.feishu.cn/callback/ws/endpoint",
        json={"AppID": settings.feishu_app_id, "AppSecret": settings.feishu_app_secret},
        timeout=15,
    )
    d = r.json()
    if d.get("code") == 0:
        print(f"ws endpoint  = OK ({d['data'].get('URL', '')[:80]}…)")
    else:
        print(f"ws endpoint  = FAIL: {d.get('code')} {d.get('msg')}")


def run() -> bool:
    """启动飞书长连接接收器（阻塞；SDK 内置自动重连）。

    返回 True 表示客户端已启动并进入阻塞（断线会重连）；
    返回 False 表示未配置（调用方不应重试）。
    """
    settings = get_settings()
    app_id = settings.feishu_app_id
    app_secret = settings.feishu_app_secret
    if not app_id or not app_secret:
        print("[feishu_ws] 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，退出")
        return False

    # run_skill 异步完成回调（2026-08-21：深度分析不阻塞对话，完成后自动发结果）
    _register_run_skill_sink()

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_handle_event_async)
        # 未订阅业务的推送事件（表情回应 created/deleted）静默忽略，避免 "processor not found" ERROR 刷屏。
        # 2026-08-21：WS 长连接事件用 v2 信封（p2.xxx 键），须 p1/p2 双注册才兜得住
        .register_p1_customized_event("im.message.reaction.created_v1", _ignore_event)
        .register_p1_customized_event("im.message.reaction.deleted_v1", _ignore_event)
        .register_p2_customized_event("im.message.reaction.created_v1", _ignore_event)
        .register_p2_customized_event("im.message.reaction.deleted_v1", _ignore_event)
        .build()
    )
    setup_file_logging()
    cli = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    print(
        f"[feishu_ws] 飞书长连接启动，目标群={settings.feishu_chat_id}，"
        f"@机器人触发盘中实时报告（项目本体直连，零 Hermes 依赖）"
    )
    cli.start()  # 阻塞；断线自动重连
    return True
