"""Фоновые задачи.

Импорт пакета настраивает брокер Dramatiq в любом процессе (и в API, и в воркере):
без этого `actor.send()` из API падает с BrokerNotFound.
"""

from app.workers import broker as broker  # noqa: F401  (side effect: dramatiq.set_broker)

__all__ = ["broker"]
