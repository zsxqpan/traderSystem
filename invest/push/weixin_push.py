# -*- coding: utf-8 -*-
"""微信(个人微信 iLink Bot API)推送通道。

API 契约从 Hermes 源码 gateway/platforms/weixin.py (v0.19.0-cn.7) 逆向还原:
  POST https://ilinkai.weixin.qq.com/ilink/bot/sendmessage
纯 stdlib,零第三方依赖。trust_env=False 直连(绕开 WinINET 系统代理)。
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import struct
import time
import urllib.request
import uuid

from invest.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"                    # iLink-App-Id
ILINK_APP_CLIENT_VERSION = 131584       # 0x020200 = "2.2.0"
CHANNEL_VERSION = "2.2.0"               # base_info.channel_version
MAX_MESSAGE_LENGTH = 2000               # 代码实测上限(文档写 4000,以代码为准)

# 消息类型常量
ITEM_TEXT = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2

_LAST_SEND: dict[str, float] = {}


def _random_wechat_uin() -> str:
    """X-WECHAT-UIN: 随机 32 位无符号整数的十进制字符串的 base64(与 Hermes 相同)。"""
    value = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _context_token_path() -> str:
    """Hermes 持久化的 context_token 文件路径(按 account+peer 存储)。"""
    settings = get_settings()
    if getattr(settings, "weixin_ctx_path", ""):
        return settings.weixin_ctx_path
    return (
        r"E:\Hermes Agent CN Desktop\data\hermes-home\weixin\accounts"
        r"\054eea562991@im.bot.context-tokens.json"
    )


def _load_context_token(to_user_id: str) -> str | None:
    try:
        with open(_context_token_path(), "r", encoding="utf-8") as fp:
            ctx = json.load(fp)
        return ctx.get(to_user_id)
    except Exception:  # noqa: BLE001
        return None


def _headers(token: str, body: str) -> dict:
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        "Authorization": "Bearer " + token,
    }


def send_text(text: str, key: str = "", min_interval: float = 0.0,
              timeout: int = 20) -> bool:
    """发送文本消息到用户微信。成功返回 True,失败返回 False(不抛异常)。

    key 相同且未到间隔则跳过(与 Notifier 限频语义一致)。
    """
    settings = get_settings()
    token = getattr(settings, "weixin_token", "") or ""
    to_user_id = getattr(settings, "weixin_to_user_id", "") or ""
    if not token or not to_user_id:
        return False
    if not text or not text.strip():
        return False
    if key:
        last = _LAST_SEND.get(key, 0.0)
        if time.time() - last < min_interval:
            return False

    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[: MAX_MESSAGE_LENGTH - 3] + "..."

    msg = {
        "from_user_id": "",
        "to_user_id": to_user_id,
        "client_id": "hermes-weixin-" + uuid.uuid4().hex,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
    }
    ctx = _load_context_token(to_user_id)
    if ctx:
        msg["context_token"] = ctx
    payload = {"msg": msg, "base_info": {"channel_version": CHANNEL_VERSION}}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    url = BASE_URL.rstrip("/") + "/ilink/bot/sendmessage"

    try:
        req = urllib.request.Request(url, data=body.encode("utf-8"),
                                     headers=_headers(token, body), method="POST")
        # 绕开 WinINET 系统代理(与项目内其它 HTTP 调用一致)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # iLink 成功响应 {"message_id": ...};失败时可能带 errcode/errmsg
        if isinstance(data, dict) and "message_id" in data:
            if key:
                _LAST_SEND[key] = time.time()
            return True
        logger.warning("微信推送被拒: %s", data)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("微信推送异常: %s", exc)
        return False
