"""structlog: одна строка JSON на событие, request_id в контексте."""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

import structlog

from app.core.config import settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def _add_request_id(_logger, _method, event_dict):
    event_dict["request_id"] = request_id_var.get()
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level.upper())

    processors = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.log_format == "console":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "app"):
    return structlog.get_logger(name)


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    return rid
