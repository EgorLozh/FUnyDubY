"""Админка: список комнат, что занимает место, уборка и удаление.

Доступ — отдельный токен из `.env` (`ADMIN_TOKEN`), заголовок `X-Admin-Token`. Токена нет в
конфиге — админка выключена: маршруты отвечают `admin_disabled`, а не открываются молча.
Сравнение токена — `hmac.compare_digest` (без утечки по времени).
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status

from app.api.deps import SessionDep
from app.core.config import settings
from app.core.errors import NotFound, ServiceUnavailable, Unauthorized
from app.models import Room
from app.schemas.admin import (
    AdminOverview,
    AdminPurgeRequest,
    AdminPurgeResult,
    AdminRoom,
    AdminRoomDeleted,
)
from app.services import admin as admin_service
from app.services import rooms as rooms_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    """Пустить только с верным админ-токеном."""
    if not settings.admin_token:
        raise ServiceUnavailable(
            "Админка отключена: задайте ADMIN_TOKEN в .env и перезапустите api",
            code="admin_disabled",
        )
    if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise Unauthorized("Неверный админ-токен", code="admin_unauthorized")


AdminDep = Annotated[None, Depends(require_admin)]


@router.get("/overview", response_model=AdminOverview, summary="Комнаты и занятое место")
async def overview(
    session: SessionDep,
    _admin: AdminDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> AdminOverview:
    data = await admin_service.overview(session, limit=limit)
    return AdminOverview(
        rooms=[AdminRoom(**entry) for entry in data["rooms"]],
        totals=data["totals"],
        disk_free_bytes=admin_service.disk_free_bytes(),
    )


@router.get("/rooms/{room_id}", response_model=AdminRoom, summary="Что лежит в комнате")
async def room_detail(session: SessionDep, room_id: str, _admin: AdminDep) -> AdminRoom:
    return AdminRoom(**await admin_service.room_detail(session, room_id))


@router.post(
    "/rooms/{room_id}/purge",
    response_model=AdminPurgeResult,
    summary="Убрать выбранные файлы комнаты",
)
async def purge_room_files(
    session: SessionDep, room_id: str, payload: AdminPurgeRequest, _admin: AdminDep
) -> AdminPurgeResult:
    room = await session.get(Room, room_id)
    if room is None:
        raise NotFound("Комната не найдена", code="room_not_found")

    report = await admin_service.purge(session, room, list(payload.scopes))
    usage = admin_service.room_usage(room.id)
    return AdminPurgeResult(
        room_id=room.id,
        scopes=report,
        freed_bytes=sum(int(item["freed_bytes"]) for item in report.values()),
        size_bytes_after=usage["size_bytes"],
        categories_after=usage["categories"],
    )


@router.delete(
    "/rooms/{room_id}",
    response_model=AdminRoomDeleted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Удалить комнату целиком",
)
async def delete_room(session: SessionDep, room_id: str, _admin: AdminDep) -> AdminRoomDeleted:
    room = await session.get(Room, room_id)
    if room is None:
        raise NotFound("Комната не найдена", code="room_not_found")
    await rooms_service.soft_delete(session, room)
    return AdminRoomDeleted(room_id=room.id, status=room.status.value)
