"""Доменные ошибки -> ответы problem+json."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Ошибка с кодом для клиента. Traceback никогда не уходит наружу."""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.extra: dict[str, Any] = extra or {}

    def to_problem(self) -> dict[str, Any]:
        return {
            "type": "about:blank",
            "title": self.detail,
            "status": self.status_code,
            "code": self.code,
            **self.extra,
        }


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class UnsupportedFormat(DomainError):
    status_code = 415
    code = "unsupported_format"


class VideoTooLong(DomainError):
    status_code = 422
    code = "video_too_long"


class NoAudioTrack(DomainError):
    status_code = 422
    code = "no_audio"


class CorruptMedia(DomainError):
    status_code = 422
    code = "corrupt_media"


class FileTooLarge(DomainError):
    status_code = 413
    code = "file_too_large"


class StorageExhausted(DomainError):
    status_code = 507
    code = "storage_exhausted"


class RateLimited(DomainError):
    status_code = 429
    code = "rate_limited"


class ServiceUnavailable(DomainError):
    status_code = 503
    code = "service_unavailable"
