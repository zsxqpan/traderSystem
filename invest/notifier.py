"""多通道推送：企业微信机器人 Webhook + 飞书群 + 个人微信（可配置同 key 限频）。

任一通道失败不影响其它通道；所有通道未配置时 send_text 返回 False。
原有 Notifier 接口（webhook/enabled/send_text）保持不变，调用方零改动。
"""
from __future__ import annotations

import logging
import time
import urllib.error

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

    def send_text(
        self,
        content: str,
        key: str = "",
        min_interval: float = 0.0,
        feishu: bool = True,
        return_results: bool = False,
        message_kind: str = "text",
        message_id: str = "",
    ) -> bool | dict[str, bool]:
        """发送文本消息到所有已配置通道；key 相同且未到间隔则跳过。

        feishu=False（2026-08-22）：跳过飞书通道（盘前报告走卡片时用，
        避免 text 与卡片重复推送）。
        默认返回聚合 bool，保持原调用兼容；return_results=True 时返回逐通道结果。
        """
        results = {"wecom": False, "weixin": False}
        if feishu:
            results["feishu"] = False
        from invest.delivery import deliver_channel, in_delivery_context

        persistent_delivery = in_delivery_context()
        stable_message_id = message_id or key or "text"
        if not content or not content.strip():
            return results if return_results else False
        if not self.enabled:
            return results if return_results else False  # 禁用状态：不尝试任何通道
        if key and not persistent_delivery:
            last = _LAST_SEND.get(key, 0.0)
            if time.time() - last < min_interval:
                return results if return_results else False

        results["wecom"] = deliver_channel(
            "wecom",
            lambda: self._send_wecom(content),
            message_kind=message_kind,
            message_id=stable_message_id,
        )
        if feishu:
            results["feishu"] = deliver_channel(
                "feishu",
                lambda: self._send_feishu(content, key=key),
                message_kind=message_kind,
                message_id=stable_message_id,
            )
        results["weixin"] = deliver_channel(
            "weixin",
            lambda: self._send_weixin(content, key=key),
            message_kind=message_kind,
            message_id=stable_message_id,
        )

        ok = any(results.values())
        if ok and key and not persistent_delivery:
            _LAST_SEND[key] = time.time()
        return results if return_results else ok

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
        except requests.RequestException as exc:
            logger.warning("企业微信推送失败: %s", exc)
            from invest.delivery import in_delivery_context

            if in_delivery_context():
                raise
            return False
        except Exception as exc:
            logger.warning("企业微信推送失败: %s", exc)
            return False

    def _send_feishu(self, content: str, *, key: str) -> bool:
        try:
            from invest.push.feishu_push import send_text as fs_send

            return fs_send(content, key=key)
        except requests.RequestException as exc:
            logger.warning("飞书群推送失败: %s", exc)
            from invest.delivery import in_delivery_context

            if in_delivery_context():
                raise
            return False
        except Exception as exc:
            logger.warning("飞书群推送失败: %s", exc)
            return False

    def _send_weixin(self, content: str, *, key: str) -> bool:
        try:
            from invest.push.weixin_push import send_text as wx_send

            return wx_send(content, key=key)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            logger.warning("微信推送失败: %s", exc)
            from invest.delivery import in_delivery_context

            if in_delivery_context():
                raise
            return False
        except Exception as exc:
            logger.warning("微信推送失败: %s", exc)
            return False
