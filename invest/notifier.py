"""企业微信机器人 Webhook 推送（可配置同 key 限频）。"""
from __future__ import annotations

import logging
import time

import requests

from invest.config import get_settings

logger = logging.getLogger(__name__)
_LAST_SEND: dict[str, float] = {}


class Notifier:
    def __init__(self, webhook: str = ""):
        settings = get_settings()
        self.webhook = webhook or settings.wecom_webhook

    @property
    def enabled(self) -> bool:
        return bool(self.webhook)

    def send_text(self, content: str, key: str = "", min_interval: float = 0.0) -> bool:
        """发送文本消息；key 相同且未到间隔则跳过。未配置 webhook 时静默返回 False。"""
        if not self.enabled:
            return False
        if key:
            last = _LAST_SEND.get(key, 0.0)
            if time.time() - last < min_interval:
                return False
        try:
            resp = requests.post(
                self.webhook,
                json={"msgtype": "text", "text": {"content": content}},
                timeout=10,
            )
            ok = resp.status_code == 200
            if ok and key:
                _LAST_SEND[key] = time.time()
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.warning("企业微信推送失败: %s", exc)
            return False