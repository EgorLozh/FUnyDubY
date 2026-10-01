"""Задачи обработки и обслуживания.

Этот модуль обязан импортировать все модули с акторами: Dramatiq CLI регистрирует
акторы только из тех модулей, что переданы ему в аргументах
(`dramatiq app.workers.broker app.workers.tasks`). Без импорта сообщения
уходят в очередь и остаются там навсегда.
"""

from app.workers.tasks import maintenance, pipeline, purge_room, render  # noqa: F401

__all__ = ["maintenance", "pipeline", "purge_room", "render"]
