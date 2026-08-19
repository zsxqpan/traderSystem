# -*- coding: utf-8 -*-
"""飞书长连接接收器（项目本体直连，lark-oapi WebSocket，零 Hermes 依赖）。

替代旧方案（Hermes 桌面端 gateway.log 轮询 / Hermes 飞书连接）。功能（2026-08-18 v4）：
1) **私聊（p2p）**：任何消息都由 Agent 回应（报告/提问/闲聊），非管理员计入每日限额；
2) **群内 @机器人**（管理员或其他人 @）：任意消息都由 Agent 回应，不再只回报告；
3) 管理员群内不 @ 的发言：仅语义识别为要实时报告才触发（纯语义，无关键词）；
4) 非管理员（群内 @ 或私聊）每日 token 限额 `FEISHU_NONADMIN_DAILY_TOKEN_LIMIT`（默认 100 万，
   记入 llm_usage job='group'），超限只回额度提示、不再消耗 token；
5) 机器人自己的消息 → 忽略。

回复分流（_agent_reply）：
- 语义识别为要实时报告 → 盘中实时报告（非管理员=公开版，无持仓警戒）；LLM 失败/超时 → 不生成；
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
    except Exception as exc:  # noqa: BLE001
        print(f"[feishu_ws] 文件日志初始化失败: {exc}")

MAX_REPORT_LEN = 3800
REPLY_MIN_INTERVAL = 30.0  # 同一发送者两次报告回复的最小间隔（秒）
CHAT_MIN_INTERVAL = 10.0   # 同一发送者两次 Agent 会话回复的最小间隔（秒）
_CHAT_MAX_LEN = 2000       # Agent 会话回复最大长度

_GREETING_KEYWORDS = ("在吗", "在不在", "你好", "您好", "hello", "hi", "help", "帮助", "你是谁", "你会什么", "怎么用")

_HELP_TEXT = (
    "在的。可以这样用我：\n"
    "· @我并说「来一份盘中报告 / 现在行情」→ 盘中实时报告（板块异动/龙头人气/核心池行情）；\n"
    "· 直接问我市场问题（如'今天哪些板块最强''怎么看军工'）→ 我会查系统数据回答；\n"
    "· 私聊我也一样。非管理员每天有 token 额度上限（100万）。"
)

_bot_open_id_cache: str = ""
_last_reply_at: dict[str, float] = {}


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
    except Exception as exc:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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


def _is_report_request(text: str, track_job: str | None = None) -> bool:
    """意图判定：**纯 LLM 语义识别**（2026-08-18 按用户要求去掉关键词触发）。

    - 只认语义：明确要「当前/实时行情报告、盘中异动、今日盘面」才返回 True；
    - LLM 失败/超时/无 key → False（此时 @ 场景仍会回帮助提示，不会完全静默）；
    - track_job 非空（非管理员）：LLMClient 带 conn，用量记入 llm_usage(job=track_job)，
      供每日 token 限额核算。
    """
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        return False


def _build_intraday_report(public: bool = False, brief: bool = True) -> str:
    """生成盘中实时报告（复用 invest.report.intraday_report）。失败返回错误说明。"""
    try:
        from invest.report import intraday_report

        db = str(ROOT / "data" / "invest.db")
        text = intraday_report(db, public=public, brief=brief)
        if len(text) > MAX_REPORT_LEN:
            text = text[:MAX_REPORT_LEN] + "\n…(截断)"
        return text
    except Exception as exc:  # noqa: BLE001
        return f"[报告生成失败: {type(exc).__name__}: {exc}]"


def _is_greeting(text: str) -> bool:
    """问候/求助类短消息 → 直接回帮助提示（本地判定，不耗 LLM token）。"""
    t = text.strip().lower()
    return len(t) <= 20 and any(k in t for k in _GREETING_KEYWORDS)


def _agent_chat(text: str, nonadmin: bool = False) -> str:
    """会话 Agent 回复（2026-08-18：Skill 由大模型语义自选并在回复中自标注）。

    非管理员计入 job='group' 限额。
    """
    try:
        from invest.agent.agents import run_chat
        from invest.db import connect

        conn = connect(str(ROOT / "data" / "invest.db"))
        try:
            out = run_chat(conn, text[:1000], job="group" if nonadmin else "feishu_chat")
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent 回复失败: %s", exc)
        return f"[Agent 回复失败: {type(exc).__name__}: {exc}]"


def _agent_reply(chat_id: str, text: str, sender_id: str, nonadmin: bool = False) -> None:
    """统一回复入口（2026-08-18）：私聊 / 群内 @ 的任意消息。

    分流：语义识别为要实时报告 → 盘中实时报告（非管理员=公开版，不计 Agent token）；
          问候/求助 → 帮助提示（本地判定，零 token）；
          其他 → 会话 Agent（run_chat）回答。
    非管理员：任何 LLM 调用都计入 job='group'，受 FEISHU_NONADMIN_DAILY_TOKEN_LIMIT（100万/日）约束。
    """
    from invest.push.feishu_push import send_message

    if nonadmin and _nonadmin_budget_exceeded():
        send_message(chat_id, "chat_id",
                     "今日非管理员 token 额度（100万）已用完，请明天再试。")
        return

    want_report = _is_report_request(text, track_job="group" if nonadmin else None)
    if want_report:
        if _rate_limited(sender_id):
            logger.info("报告请求限频跳过（30s 内重复）: %s", text[:30])
            return
        # 2026-08-18 方案E：默认简洁版；明确要"详细/完整"才发完整版（私聊/群聊一致）
        detailed = any(k in text for k in ("详细", "完整", "详细版", "完整版"))
        logger.info("盘中报告请求（nonadmin=%s brief=%s）: %s", nonadmin, not detailed, text[:40])
        send_message(chat_id, "chat_id", "⏳ 收到，正在生成盘中实时报告…")
        report = _build_intraday_report(public=nonadmin, brief=not detailed)
        ok = send_message(chat_id, "chat_id", report)
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
    reply = _agent_chat(text, nonadmin=nonadmin)
    # Skill 由大模型语义自选并在回复文本末尾自标注（"↘ 已使用 Skill：xxx"），原样发送
    ok = send_message(chat_id, "chat_id", reply)
    logger.info("Agent 回复完成 ok=%s len=%d", ok, len(reply))


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

    from invest.push.feishu_push import send_message

    # ---- 私聊（p2p）：任何消息都回应（非管理员计入每日限额）----
    if getattr(msg, "chat_type", "") == "p2p":
        _agent_reply(chat_id, text, sender_id, nonadmin=sender_id != owner_open_id)
        return

    if chat_id != target_chat:
        return  # 只处理目标群

    mentioned = _is_mentioned(msg)

    if sender_id == owner_open_id:
        # ---- 管理员 ----
        # 兜底：mentions 因权限/SDK 解析缺失时，文本里飞书占位符 @_user_N 也算被艾特
        mentioned = mentioned or ("@_user" in text and "@" in text)
        if mentioned:
            # 群内 @ 管理员：任意消息都由 Agent 回应（报告/闲聊/提问）
            _agent_reply(chat_id, text, sender_id, nonadmin=False)
        elif _is_report_request(text):
            # 未 @ 的管理员发言：仅语义识别为要实时报告才触发
            if _rate_limited(sender_id):
                logger.info("管理员请求限频跳过（30s 内重复）: %s", text[:30])
                return
            logger.info("管理员请求盘中报告（未@）: %s", text[:40])
            send_message(chat_id, "chat_id", "⏳ 收到，正在生成盘中实时报告…")
            report = _build_intraday_report()
            ok = send_message(chat_id, "chat_id", report)
            logger.info("盘中报告回复完成 ok=%s", ok)
        return  # 管理员普通发言（未 @ 且非报告）不回复

    # ---- 非管理员（限额 100 万/日，超限只回额度提示）----
    if mentioned:
        _agent_reply(chat_id, text, sender_id, nonadmin=True)
    # 未 @ 的非管理员消息：忽略（不打扰群聊）


def _handle_event_async(data: P2ImMessageReceiveV1) -> None:
    """包装：事件处理放入守护线程，不阻塞 SDK 的事件循环（报告生成较耗时）。

    线程内异常必须落日志（pythonw 无控制台，异常默认不可见——2026-08-18 排查修复）。
    2026-08-18：处理前先给消息加 ❤️ 表情回应（告知已收到；失败静默），需权限 im:message.reaction。
    """

    def _wrapper() -> None:
        try:
            msg = getattr(getattr(data, "event", None), "message", None)
            if msg is not None and getattr(msg, "message_id", None):
                try:
                    from invest.push.feishu_push import add_reaction

                    add_reaction(msg.message_id, "HEART")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("消息表情回应失败: %s", exc)
            _handle_event(data)
        except Exception as exc:  # noqa: BLE001
            logger.exception("事件处理异常: %s", exc)

    threading.Thread(target=_wrapper, daemon=True).start()


def _ignore_event(_data) -> None:
    """忽略未订阅业务的事件（如表情回应 created/deleted），避免日志 ERROR 噪音。"""
    pass


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

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_handle_event_async)
        # 未订阅业务的推送事件（表情回应等）静默忽略，避免 "processor not found" ERROR 刷屏
        .register_p1_customized_event("im.message.reaction.created_v1", _ignore_event)
        .register_p1_customized_event("im.message.reaction.deleted_v1", _ignore_event)
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
