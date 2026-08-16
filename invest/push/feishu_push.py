# -*- coding: utf-8 -*-
"""飞书群推送通道(开放平台 API 直连)。

依赖:requests;必须走 127.0.0.1:7892 代理(open.feishu.cn 直连被网络阻断,
2026-08-15 实测)。trust_env=False 避免读 WinINET 代理(此处显式指定代理)。
"""
from __future__ import annotations

import json
import logging
import time

import requests

from invest.config import get_settings

logger = logging.getLogger(__name__)

_LAST_SEND: dict[str, float] = {}
_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.trust_env = False
        settings = get_settings()
        proxy = getattr(settings, "feishu_proxy", "") or "http://127.0.0.1:7892"
        s.proxies = {"http": proxy, "https": proxy}
        _SESSION = s
    return _SESSION


def _tenant_token() -> str | None:
    settings = get_settings()
    app_id = getattr(settings, "feishu_app_id", "") or ""
    app_secret = getattr(settings, "feishu_app_secret", "") or ""
    if not app_id or not app_secret:
        return None
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        r = _session().post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
        d = r.json()
        if d.get("code") != 0:
            logger.warning("飞书 token 获取失败: code=%s msg=%s", d.get("code"), d.get("msg"))
            return None
        return d["tenant_access_token"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("飞书 token 获取异常: %s", exc)
        return None


def send_text(text: str, key: str = "", min_interval: float = 0.0) -> bool:
    """发送文本消息到飞书群。成功返回 True,失败返回 False(不抛异常)。"""
    settings = get_settings()
    chat_id = getattr(settings, "feishu_chat_id", "") or ""
    if not chat_id:
        return False
    if not text or not text.strip():
        return False
    if key:
        last = _LAST_SEND.get(key, 0.0)
        if time.time() - last < min_interval:
            return False

    token = _tenant_token()
    if not token:
        return False

    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    try:
        r = _session().post(
            url,
            params={"receive_id_type": "chat_id"},
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        d = r.json()
        if d.get("code") != 0:
            logger.warning("飞书群推送被拒: code=%s msg=%s", d.get("code"), d.get("msg"))
            return False
        if key:
            _LAST_SEND[key] = time.time()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("飞书群推送异常: %s", exc)
        return False
