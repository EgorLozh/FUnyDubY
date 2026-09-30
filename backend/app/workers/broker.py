"""Брокер фоновых задач (Redis + Dramatiq)."""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AgeLimit, CurrentMessage, Retries, TimeLimit

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()

broker = RedisBroker(url=settings.redis_url, middleware=[
    AgeLimit(max_age=900_000),          # 15 минут на задачу
    TimeLimit(time_limit=900_000),
    CurrentMessage(),
    Retries(max_retries=2, min_backoff=30_000, max_backoff=120_000),
])

dramatiq.set_broker(broker)
