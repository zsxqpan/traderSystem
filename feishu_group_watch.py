# -*- coding: utf-8 -*-
"""
飞书群消息监控器 + 盘中实时报告机器人 (feishu_group_watch.py)
=============================================================
原理: Hermes 飞书适配器收到群内任何消息都会先写 gateway.log
      (Inbound group message received ...),之后才做授权过滤。
      本脚本增量读取该日志,做两件事:
      1) 把"非潘军桥本人、非 Hermes 自己"的群消息实时转发到潘军桥的飞书 DM;
      2) 当潘军桥(你)在群里艾特机器人/发送关键词(盘中/实时/报告/行情)时,
         生成 A股投资系统的盘中实时报告并回复到群里(由 invest.report 提供)。

用法:
  python feishu_group_watch.py              # 前台运行
  python feishu_group_watch.py --once       # 只处理一次增量(供 cron 调用)
"""
import argparse
import ast
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
LOG_PATH = Path(r"E:\Hermes Agent CN Desktop\data\hermes-home\logs\gateway.log")
STATE_PATH = Path(r"E:\Hermes Agent CN Desktop\data\hermes-home\feishu_watch_state.json")
TARGET_CHAT_ID = "oc_2191375d2b58945a3611c536d3b44ef5"   # hermes-agent 群
OWNER_OPEN_ID = "ou_294d8e0fe6d9d74cd7a71c2913ca68eb"   # 潘军桥(你/转发目标)
SELF_BOT_OPEN_ID = "ou_e4f4b318f5fe802ab5170102a9343f29"  # Hermes 自己(Trader-Fox)

APP_ID = "cli_aa0abf5ab7399bd8"
APP_SECRET = "6ECyVsQrtmFqlav4h3VEycK3N1T6qybu"
PROXY = "http://127.0.0.1:7892"

# 盘中实时报告触发关键词（你艾特机器人或发送这些词即回复报告）
REPORT_KEYWORDS = ("盘中", "实时", "报告", "行情", "盘面", "现在市场", "今日市场")
# 报告最大长度（企业微信/飞书文本上限安全值）
MAX_REPORT_LEN = 3800

# 已知身份表(用于转发时标注发送者是谁)
KNOWN_IDS = {
    "ou_294d8e0fe6d9d74cd7a71c2913ca68eb": "潘军桥(你)",
    "ou_e9c37732a5bb271b6650a5cad5007b63": "许永乐",
    "ou_e4f4b318f5fe802ab5170102a9343f29": "Trader-Fox(Hermes)",
}

# gateway.log 消息行格式:
# [Feishu] Inbound group message received: id=om_xxx type=text chat_id=oc_xxx sender=user:ou_xxx text='...' media=0
MSG_RE = re.compile(
    r"\[Feishu\] Inbound group message received: "
    r"id=(\S+) type=(\S+) chat_id=(\S+) "
    r"sender=(user|bot):(\S+) text=(.*?) media=(\d+)"
)

# ---------------------------------------------------------------------------
# 飞书 API
# ---------------------------------------------------------------------------
_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.proxies = {"http": PROXY, "https": PROXY}


def get_tenant_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    r = _SESSION.post(url, json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=10)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"token failed: {d}")
    return d["tenant_access_token"]


def send_chat(token: str, chat_id: str, text: str) -> None:
    """发送文本到群/会话（receive_id_type=chat_id）。"""
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    r = _SESSION.post(
        url, params={"receive_id_type": "chat_id"}, json=body,
        headers={"Authorization": f"Bearer {token}"}, timeout=10,
    )
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"send_chat failed: code={d.get('code')} msg={d.get('msg')}")


def send_dm(token: str, open_id: str, text: str) -> None:
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    body = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    r = _SESSION.post(
        url, params={"receive_id_type": "open_id"}, json=body,
        headers={"Authorization": f"Bearer {token}"}, timeout=10,
    )
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"send_dm failed: code={d.get('code')} msg={d.get('msg')}")


def parse_text_literal(raw: str) -> str:
    """日志里 text=%r 是 Python repr 格式,安全还原。"""
    try:
        return ast.literal_eval(raw)
    except Exception:
        return raw.strip("'\"")


def load_state() -> int:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8")).get("offset", 0)
    except Exception:
        return 0


def save_state(offset: int) -> None:
    STATE_PATH.write_text(json.dumps({"offset": offset, "updated": time.time()}), encoding="utf-8")


def sender_label(sender_type: str, sender_id: str) -> str:
    if sender_type == "bot":
        return f"🤖 机器人 {KNOWN_IDS.get(sender_id, sender_id[:20])}"
    return f"👤 {KNOWN_IDS.get(sender_id, sender_id[:20])}"


def build_intraday_report() -> str:
    """生成盘中实时报告（复用 A股投资系统 invest.report）。失败返回空串。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from invest.report import intraday_report
        db = os.environ.get("DB_PATH", str(Path(__file__).resolve().parent / "data" / "invest.db"))
        text = intraday_report(db)
        if len(text) > MAX_REPORT_LEN:
            text = text[:MAX_REPORT_LEN] + "\n…(截断)"
        return text
    except Exception as exc:  # noqa: BLE001
        return f"[报告生成失败: {type(exc).__name__}: {exc}]"


def _is_report_request(text: str) -> bool:
    """判断是否为盘中实时报告请求：艾特机器人 或 含触发关键词。"""
    t = text or ""
    if "@" in t:  # 艾特机器人（飞书 @ 以 @ 开头）
        return True
    return any(kw in t for kw in REPORT_KEYWORDS)


def scan_once(token: str) -> dict:
    """扫描日志增量。返回 {forwarded, replied}。"""
    if not LOG_PATH.exists():
        return {"forwarded": 0, "replied": 0}
    offset = load_state()
    size = LOG_PATH.stat().st_size
    if offset > size:  # 日志轮转过,从头读(只读尾部避免爆炸)
        offset = max(0, size - 200_000)

    forwarded = 0
    replied = 0
    with LOG_PATH.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        for line in f:
            m = MSG_RE.search(line)
            if not m:
                continue
            msg_id, msg_type, chat_id, sender_type, sender_id, text_raw, _media = m.groups()
            if chat_id != TARGET_CHAT_ID:
                continue
            text = parse_text_literal(text_raw)
            if not text.strip():
                continue

            # 你（OWNER）艾特机器人/发关键词 → 回复盘中实时报告到群里
            if sender_id == OWNER_OPEN_ID:
                if _is_report_request(text):
                    try:
                        report = build_intraday_report()
                        send_chat(token, TARGET_CHAT_ID, report)
                        replied += 1
                        print(f"[{time.strftime('%H:%M:%S')}] 已回复盘中实时报告（请求: {text[:30]}）")
                    except Exception as e:
                        print(f"[!] 报告回复失败 {msg_id}: {e}", file=sys.stderr)
                continue  # 你自己的普通消息不转发

            if sender_id == SELF_BOT_OPEN_ID:  # Hermes 自己发言,不转发
                continue

            who = sender_label(sender_type, sender_id)
            out = f"📡 [群监控·hermes-agent]\n{who}:\n{text[:500]}"
            try:
                send_dm(token, OWNER_OPEN_ID, out)
                forwarded += 1
                print(f"[{time.strftime('%H:%M:%S')}] 转发 {sender_type}:{sender_id[:12]} -> {text[:40]}")
            except Exception as e:
                print(f"[!] 转发失败 {msg_id}: {e}", file=sys.stderr)
        offset = f.tell()
    save_state(offset)
    return {"forwarded": forwarded, "replied": replied}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只处理一次增量后退出")
    args = ap.parse_args()

    token = get_tenant_token()
    if args.once:
        scan_once(token)
        return

    print(f"群监控+报告机器人启动,目标群={TARGET_CHAT_ID},艾特机器人回复盘中实时报告")
    while True:
        try:
            scan_once(token)
        except Exception as e:
            print(f"[!] scan error: {e}", file=sys.stderr)
            time.sleep(15)
            continue
        time.sleep(3)


if __name__ == "__main__":
    main()
