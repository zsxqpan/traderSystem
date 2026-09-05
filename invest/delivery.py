"""逐通道持久投递回执与保守崩溃恢复。"""
from __future__ import annotations

import contextvars
import urllib.error
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

import requests

from invest.db import connect

DeliveryState = Literal["succeeded", "failed", "uncertain"]


@dataclass
class DeliveryContext:
    db_path: str
    job: str
    scheduled_date: str
    run_slot: str
    uncertain_channels: set[str] = field(default_factory=set)
    channel_states: dict[str, DeliveryState] = field(default_factory=dict)

    def deliver(
        self,
        channel: str,
        sender: Callable[[], bool],
        *,
        message_kind: str,
        message_id: str,
    ) -> bool:
        """发送一个通道；崩溃遗留 sending 转 uncertain 且不自动重发。"""
        result_key = f"{message_kind}/{message_id}/{channel}"
        receipt_key = (
            self.job,
            self.scheduled_date,
            self.run_slot,
            message_kind,
            message_id,
            channel,
        )
        conn = connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT status FROM delivery_receipts
                   WHERE job=? AND scheduled_date=? AND run_slot=?
                     AND message_kind=? AND message_id=? AND channel=?""",
                receipt_key,
            ).fetchone()
            status = str(row["status"]) if row else ""
            if status == "succeeded":
                conn.commit()
                self.channel_states[result_key] = "succeeded"
                return True
            if status in {"sending", "uncertain"}:
                conn.execute(
                    """UPDATE delivery_receipts SET
                           status='uncertain',
                           detail='上次发送结果未知，需人工核验',
                           updated_at=datetime('now','localtime')
                       WHERE job=? AND scheduled_date=? AND run_slot=?
                         AND message_kind=? AND message_id=? AND channel=?""",
                    receipt_key,
                )
                conn.commit()
                self.uncertain_channels.add(channel)
                self.channel_states[result_key] = "uncertain"
                return False
            if row:
                conn.execute(
                    """UPDATE delivery_receipts SET
                           status='sending', attempt=attempt + 1, detail='',
                           started_at=datetime('now','localtime'),
                           updated_at=datetime('now','localtime')
                       WHERE job=? AND scheduled_date=? AND run_slot=?
                         AND message_kind=? AND message_id=? AND channel=?""",
                    receipt_key,
                )
            else:
                conn.execute(
                    """INSERT INTO delivery_receipts(
                           job, scheduled_date, run_slot, message_kind, message_id,
                           channel, status, attempt, started_at, updated_at
                       ) VALUES(?, ?, ?, ?, ?, ?, 'sending', 1,
                                datetime('now','localtime'), datetime('now','localtime'))""",
                    receipt_key,
                )
            conn.commit()
        finally:
            conn.close()

        detail = ""
        try:
            ok = bool(sender())
            final_status: DeliveryState = "succeeded" if ok else "failed"
        except (
            requests.RequestException,
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            ok = False
            final_status = "uncertain"
            detail = str(exc)
            self.uncertain_channels.add(channel)
        except Exception as exc:
            ok = False
            final_status = "failed"
            detail = str(exc)
        self.channel_states[result_key] = final_status

        conn = connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    """UPDATE delivery_receipts SET
                           status=?, detail=?, succeeded_at=CASE WHEN ? THEN
                               datetime('now','localtime') ELSE succeeded_at END,
                           updated_at=datetime('now','localtime')
                       WHERE job=? AND scheduled_date=? AND run_slot=?
                         AND message_kind=? AND message_id=? AND channel=?""",
                    (
                        final_status,
                        detail,
                        int(ok),
                        *receipt_key,
                    ),
                )
        finally:
            conn.close()
        return ok


_CURRENT: contextvars.ContextVar[DeliveryContext | None] = contextvars.ContextVar(
    "delivery_context",
    default=None,
)


@contextmanager
def delivery_context(
    db_path: str,
    job: str,
    scheduled_date: str,
    run_slot: str,
) -> Iterator[DeliveryContext]:
    context = DeliveryContext(db_path, job, scheduled_date, run_slot)
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


def deliver_channel(
    channel: str,
    sender: Callable[[], bool],
    *,
    message_kind: str = "task",
    message_id: str = "default",
) -> bool:
    """有任务上下文时走持久回执，否则保持原直接发送语义。"""
    context = _CURRENT.get()
    if context is None:
        return bool(sender())
    return context.deliver(
        channel,
        sender,
        message_kind=message_kind,
        message_id=message_id,
    )


def in_delivery_context() -> bool:
    return _CURRENT.get() is not None
