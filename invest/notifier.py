"""多通道推送：企业微信机器人 Webhook + 飞书群 + 个人微信（可配置同 key 限频）。

任一通道失败不影响其它通道；所有通道未配置时 send_text 返回 False。
原有 Notifier 接口（webhook/enabled/send_text）保持不变，调用方零改动。
"""
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
        """发送文本消息到所有已配置通道；key 相同且未到间隔则跳过。

        返回 True 表示至少一个通道成功；全部失败或全部未配置时返回 False。
        """
        if not content or not content.strip():
            return False
        if not self.enabled:
            return False  # 禁用状态：不尝试任何通道
        if key:
            last = _LAST_SEND.get(key, 0.0)
            if time.time() - last < min_interval:
                return False

        results: list[bool] = []
        results.append(self._send_wecom(content))
        results.append(self._send_feishu(content, key=key))
        results.append(self._send_weixin(content, key=key))

        ok = any(results)
        if ok and key:
            _LAST_SEND[key] = time.time()
        return ok

    # --- 通道实现 ---------------------------------------------------------

    def _send_wecom(self, content: str) -> bool:
        try:
            # trust_env=False：忽略 Windows 系统代理（WinINET），
            # 否则代理软件未运行时企业微信推送全部被拒（2026-08-15 实测同实时行情）
            sess = requests.Session()
            sess.trust_env = False
            resp = sess.post(
                self.webhook,
                json={"msgtype": "text", "text": {"content": content}},
                timeout=10,
            )
            ok = resp.status_code == 200
            if not ok:
                logger.warning("企业微信推送失败: HTTP %s", resp.status_code)
            return ok
        except Exception as exc:
            logger.warning("企业微信推送失败: %s", exc)
            return False

    def _send_feishu(self, content: str, *, key: str) -> bool:
        try:
            from invest.push.feishu_push import send_text as fs_send

            return fs_send(content, key=key)
        except Exception as exc:
            logger.warning("飞书群推送失败: %s", exc)
            return False

    def _send_weixin(self, content: str, *, key: str) -> bool:
        try:
            from invest.push.weixin_push import send_text as wx_send

            return wx_send(content, key=key)
        except Exception as exc:
            logger.warning("微信推送失败: %s", exc)
            return False
