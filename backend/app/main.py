"""FastAPI-приложение: маршруты, обработчики ошибок, логирование запросов."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import (
    assignments,
    events,
    health,
    jobs,
    lines,
    media,
    recordings,
    rooms,
    video,
)
from app.core.config import settings
from app.core.db import engine
from app.core.errors import DomainError
from app.core.logging import configure_logging, get_logger, new_request_id, request_id_var
from app.core.redis import close_redis

configure_logging()
log = get_logger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    log.info(
        "app_start",
        env=settings.app_env,
        version=__version__,
        storage_root=str(settings.storage_root),
        separation_model=settings.separation_model,
        stt_model=settings.stt_model,
        diarization_model=settings.diarization_model,
        hf_token=bool(settings.hf_token),
    )
    # Проверить окружение на «склеенные» значения (частая ошибка ручного .env)
    try:
        from app.scripts.check_env import validate_runtime

        env_problems = validate_runtime()
        for problem in env_problems:
            log.error("env_problem", detail=problem)
    except Exception as exc:  # noqa: BLE001
        log.warning("env_check_skipped", error=str(exc))

    # Запустить самопланирующийся реконсилятор очереди (идемпотентно через Redis-ключ)
    try:
        from app.workers.tasks.maintenance import bootstrap as maintenance_bootstrap

        maintenance_bootstrap()
    except Exception as exc:  # noqa: BLE001 — обслуживание не должно мешать API стартовать
        log.warning("maintenance_bootstrap_skipped", error=str(exc))
    yield
    await close_redis()
    await engine.dispose()
    log.info("app_stop")

app = FastAPI(
    title="Collaborative Video Dubbing Platform",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = new_request_id()
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
    except DomainError:
        raise
    except Exception:
        log.exception("unhandled_error", path=request.url.path, method=request.method)
        raise
    finally:
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
    response.headers["X-Request-ID"] = request_id_var.get() or rid
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_problem())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "type": "about:blank",
            "title": "Некорректный запрос",
            "status": 422,
            "code": "validation_error",
            "errors": [
                {"loc": list(err.get("loc", [])), "msg": err.get("msg", "")} for err in exc.errors()
            ],
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_exception", error=f"{type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "type": "about:blank",
            "title": "Внутренняя ошибка сервера",
            "status": 500,
            "code": "internal_error",
        },
    )


app.include_router(health.router)
app.include_router(rooms.router)
app.include_router(video.router)
app.include_router(jobs.router)
app.include_router(lines.router)
app.include_router(assignments.router)
app.include_router(media.router)
app.include_router(recordings.router)
app.include_router(events.router)
