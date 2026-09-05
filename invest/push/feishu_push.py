"""飞书群推送通道(开放平台 API 直连，零 Hermes 依赖)。

依赖:requests。直连 open.feishu.cn(2026-08-16 实测直连可用,
不再依赖 FastLink 代理;8/15 曾误判直连被阻断,实为 DNS/代理残留)。
trust_env=False 避免读 WinINET 注册表代理;NO_PROXY 已含 feishu.cn。
2026-08-18: tenant_access_token 加缓存(2h 有效,提前 60s 过期) + 发送失败重试 1 次。
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

# tenant_access_token 缓存: {token: expires_at}（有效期 2h，提前 60s 视为过期）
_TOKEN_CACHE: dict[str, float] = {}
_TOKEN_TTL = 2 * 3600 - 60


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.trust_env = False
        # 直连：不设置 proxies（实测 open.feishu.cn 可直连）
        _SESSION = s
    return _SESSION


def _tenant_token(force: bool = False) -> str | None:
    """获取 tenant_access_token（带 2h 缓存，避免每条消息都重新换取）。"""
    now = time.time()
    if not force:
        for token, expires_at in list(_TOKEN_CACHE.items()):
            if expires_at > now:
                return token
            _TOKEN_CACHE.pop(token, None)

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
        token = d["tenant_access_token"]
        _TOKEN_CACHE[token] = now + _TOKEN_TTL
        return token
    except Exception as exc:
        logger.warning("飞书 token 获取异常: %s", exc)
        return None


def _post_message(token: str, receive_id: str, receive_id_type: str, body: dict) -> dict | None:
    """发送一条消息，返回响应 JSON；网络异常返回 None。"""
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    try:
        r = _session().post(
            url,
            params={"receive_id_type": receive_id_type},
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        return r.json()
    except requests.RequestException as exc:
        logger.warning("飞书推送异常: %s", exc)
        from invest.delivery import in_delivery_context

        if in_delivery_context():
            raise
        return None
    except Exception as exc:
        logger.warning("飞书推送异常: %s", exc)
        return None


def send_message(receive_id: str, receive_id_type: str, text: str) -> bool:
    """发送文本到指定会话（群/单聊）。

    receive_id_type: chat_id / open_id / user_id / email。
    成功返回 True,失败返回 False(不抛异常)。
    2026-08-18: token 失效/网络抖动时重试一次（换新 token + 重发）。
    """
    if not text or not text.strip():
        return False

    token = _tenant_token()
    if not token:
        return False

    body = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    d = _post_message(token, receive_id, receive_id_type, body)
    if d is not None and d.get("code") == 0:
        return True

    # 失败重试 1 次：换新 token（可能是 token 失效/过期），等 1s 再发
    logger.warning("飞书推送首次失败(code=%s)，重试 1 次", (d or {}).get("code"))
    time.sleep(1)
    token = _tenant_token(force=True)
    if not token:
        return False
    d = _post_message(token, receive_id, receive_id_type, body)
    if d is not None and d.get("code") == 0:
        return True
    logger.warning("飞书推送重试仍失败: %s", (d or {}).get("msg"))
    return False


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

    ok = send_message(chat_id, "chat_id", text)
    if ok and key:
        _LAST_SEND[key] = time.time()
    return ok


def send_post(receive_id: str, receive_id_type: str, segments: list[dict]) -> bool:
    """发送 post 富文本消息（2026-08-18，用于 Skill 标注等弱化行）。

    segments: 消息段列表，每段 {"tag":"text","text":...,"style":[可选]}；
    会把各段拼进一个 post 消息（最后一段自动换行分隔）。
    """
    if not segments:
        return False
    token = _tenant_token()
    if not token:
        return False
    content = {
        "post": {
            "zh_cn": {
                "title": "",
                "content": [segments],
            }
        }
    }
    body = {"receive_id": receive_id, "msg_type": "post",
            "content": json.dumps(content, ensure_ascii=False)}
    d = _post_message(token, receive_id, receive_id_type, body)
    if d is not None and d.get("code") == 0:
        return True
    logger.warning("飞书 post 消息失败(code=%s)", (d or {}).get("code"))
    return False


def send_card(receive_id: str, receive_id_type: str, card: dict) -> bool:
    """发送 interactive 卡片消息（2026-08-22，盘前报告表格/加粗用）。

    card: 卡片 JSON 2.0 结构（schema=2.0，body.elements 含 div/lark_md、table 组件）。
    发送失败返回 False（调用方可回退 text/post）。
    """
    if not card:
        return False
    token = _tenant_token()
    if not token:
        return False
    body = {"receive_id": receive_id, "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False)}
    d = _post_message(token, receive_id, receive_id_type, body)
    if d is not None and d.get("code") == 0:
        return True
    logger.warning("飞书卡片消息失败(code=%s)", (d or {}).get("code"))
    return False


def upload_image(image_bytes: bytes, image_type: str = "message") -> str | None:
    """上传图片（消息用），返回 image_key（供卡片 image 组件）；失败返回 None。

    2026-08-22：B1 盘中报告图表（matplotlib PNG → 卡片展示）。
    需要权限 im:image（开发者后台）。
    """
    if not image_bytes:
        return None
    token = _tenant_token()
    if not token:
        return None
    url = "https://open.feishu.cn/open-apis/im/v1/images"
    try:
        r = _session().post(
            url,
            data={"image_type": image_type},
            files={"image": ("chart.png", image_bytes, "image/png")},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        d = r.json()
        if d.get("code") == 0:
            return d.get("data", {}).get("image_key")
        logger.warning("飞书图片上传失败: code=%s msg=%s", d.get("code"), d.get("msg"))
    except Exception as exc:
        logger.warning("飞书图片上传异常: %s", exc)
    return None


def add_reaction(message_id: str, emoji: str = "HEART") -> bool:
    """给消息添加表情回应（2026-08-18：收到消息先回 ❤️，告知已收到）。

    需要权限 im:message.reaction（开发者后台开启）。失败静默（仅日志）。
    emoji: 飞书 emoji_type 枚举，默认 HEART（爱心）。
    """
    if not message_id:
        return False
    token = _tenant_token()
    if not token:
        return False
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions"
    try:
        r = _session().post(
            url,
            json={"reaction_type": {"emoji_type": emoji}},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        d = r.json()
        if d.get("code") != 0:
            logger.warning("飞书表情回应失败: code=%s msg=%s", d.get("code"), d.get("msg"))
            return False
        return True
    except Exception as exc:
        logger.warning("飞书表情回应异常: %s", exc)
        return False
