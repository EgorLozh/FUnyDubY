"""Сборка финальной озвучки: заявка, история, статус, отмена, скачивание.

Рендер — тяжёлая операция на воркере (`cpu`), поэтому POST только ставит задачу и сразу
возвращает её состояние: интерфейс следит за прогрессом по SSE (`render.updated`).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import Response
from app.api.deps import ParticipantDep, RoomDep, SessionDep
from app.api.files import file_response
from app.core.errors import Conflict, NotFound
from app.models import RenderJob, RenderStatus
from app.schemas.render import RenderCreate, RenderOut, render_to_out
from app.services import renders as renders_service
from app.services import storage

router = APIRouter(prefix="/api/rooms", tags=["renders"])


def _send(render_id: uuid.UUID) -> None:
    from app.workers.tasks.render import render_final

    render_final.send(str(render_id))


@router.post(
    "/{room_id}/renders",
    response_model=RenderOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Собрать озвучку",
)
async def create_render(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    payload: RenderCreate | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RenderOut:
    options = payload.options.model_dump(exclude_none=True) if payload and payload.options else None
    render, reused = await renders_service.create_render(session, room, participant.id, options)
    await session.commit()
    if not reused:
        _send(render.id)
    return render_to_out(render)


@router.get("/{room_id}/renders", response_model=list[RenderOut], summary="История сборок")
async def list_renders(room: RoomDep, session: SessionDep, participant: ParticipantDep) -> list[RenderOut]:
    rows = await renders_service.list_renders(session, room.id)
    return [render_to_out(row) for row in rows]


@router.get("/{room_id}/renders/{render_id}", response_model=RenderOut, summary="Статус сборки")
async def get_render(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    render_id: uuid.UUID,
) -> RenderOut:
    render = await renders_service.get_render(session, room.id, render_id)
    return render_to_out(render)


@router.post("/{room_id}/renders/{render_id}/cancel", response_model=RenderOut, summary="Отменить сборку")
async def cancel_render(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    render_id: uuid.UUID,
) -> RenderOut:
    render = await renders_service.cancel_render(session, room, render_id)
    await session.commit()
    return render_to_out(render)


@router.get("/{room_id}/renders/{render_id}/file", summary="Скачать готовое видео")
async def download_render(
    request: Request,
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    render_id: uuid.UUID,
) -> Response:
    render = await renders_service.get_render(session, room.id, render_id)
    if render.status != RenderStatus.DONE or not render.output_path:
        raise Conflict("Результат ещё не готов", code="render_not_ready")
    path = storage.absolute(render.output_path)
    if not path.exists():
        raise NotFound("Файл результата пропал", code="render_file_missing")
    filename = f"{room.id}-dub-{str(render.id)[:8]}.mp4"
    return file_response(request, path, content_type="video/mp4", filename=filename, attachment=True)
