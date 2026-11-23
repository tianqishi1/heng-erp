"""Outbox relay + 进程内事件总线（M0）。生产：独立 relay worker → NATS JetStream。"""
from __future__ import annotations
import datetime as dt
from collections import defaultdict
from typing import Callable
from sqlalchemy import select
from .db import Outbox, utcnow


class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Callable]] = defaultdict(list)
        self.published: list[dict] = []  # 测试/审计可观测

    def subscribe(self, event_type: str, fn: Callable):
        self._subs[event_type].append(fn)

    def publish(self, event: dict):
        self.published.append(event)
        for fn in self._subs.get(event["type"], []) + self._subs.get("*", []):
            fn(event)


def drain_outbox(session, bus: EventBus) -> int:
    rows = session.execute(select(Outbox).where(Outbox.published_at.is_(None)).order_by(Outbox.id)).scalars().all()
    n = 0
    for row in rows:
        bus.publish(row.payload)
        row.published_at = utcnow()
        n += 1
    session.commit()
    return n
