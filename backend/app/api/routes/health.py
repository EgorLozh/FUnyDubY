"""Health/readiness: то, что должно быть зелёным до старта любой работы."""

from __future__ import annotations

import shutil

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import __version__
from app.core.config import settings
from app.core.db import check_database
from app.core.redis import check_redis
from app.services import storage

router = APIRouter(prefix="/api", tags=["health"])


def _gpu_info() -> dict[str, object]:
    """GPU видна, если есть nvidia-smi; torch тянем только в ML-образе."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return {"available": False, "reason": "nvidia-smi not found"}
    import subprocess

    try:
        out = subprocess.run(  # noqa: S603
            [smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        return {"available": True, "devices": out.splitlines()}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


@router.get("/health")
async def health() -> dict[str, object]:
    """Liveness: процесс жив, ничего не проверяем."""
    return {"status": "ok", "version": __version__, "env": settings.app_env}


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: БД, Redis, диск. 503, если хоть что-то не готово."""
    db_ok, db_info = await check_database()
    redis_ok, redis_info = await check_redis()
    free = storage.free_bytes()
    disk_ok = free >= settings.min_free_disk_bytes
    storage_ok = settings.storage_root.exists() and storage.storage_root_is_writable()

    ready_ = db_ok and redis_ok and disk_ok and storage_ok
    body = {
        "status": "ready" if ready_ else "not_ready",
        "version": __version__,
        "checks": {
            "database": {"ok": db_ok, "info": db_info},
            "redis": {"ok": redis_ok, "info": redis_info},
            "storage": {
                "ok": storage_ok,
                "root": str(settings.storage_root),
                "free_bytes": free,
                "min_free_bytes": settings.min_free_disk_bytes,
            },
            "gpu": _gpu_info(),
        },
    }
    return JSONResponse(status_code=200 if ready_ else 503, content=body)
