"""Тейки озвучки: загрузка с микрофона, список, актуализация, удаление.

Ключевое требование архитектуры: **запись не может быть длиннее реплики**. Проверяем это на
сервере (клиентский автостоп — удобство, а не гарантия): если длительность присланного файла
больше длительности реплики на `record_tolerance_ms`, отклоняем с понятной ошибкой. Допуск
нужен из-за хвоста энкодера MediaRecorder.

Принятый файл нормализуется к ровно нужной длительности (`atrim` + `apad`) и громкости
(`loudnorm`), так что в финальном миксе тейк занимает точно свой интервал.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Header, Path as PathParam, Query, UploadFile, status
from fastapi.responses import Response

from app.api.deps import ParticipantDep, RoomDep, SessionDep
from app.core.config import settings
from app.core.errors import Conflict, DomainError, UnsupportedFormat
from app.core.logging import get_logger
from app.schemas.recording import RecordingOut, recording_to_out
from app.services import assignments as assignments_service
from app.services import lines as lines_service
from app.services import recordings as recordings_service
from app.services import storage
from app.services.rooms import emit_event
from app.video import ffmpeg, probe

log = get_logger("api.recordings")

router = APIRouter(prefix="/api/rooms", tags=["recordings"])

ALLOWED_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "application/ogg",
    "video/webm",  # некоторые сборки браузеров отдают webm именно так
}
ALLOWED_SUFFIXES = {".webm", ".ogg", ".oga", ".mp4", ".m4a", ".wav", ".mp3"}


class RecordingTooLong(DomainError):
    status_code = 422
    code = "recording_too_long"


class NoSpeechInTake(DomainError):
    status_code = 422
    code = "take_silent"


def _suffix(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix in ALLOWED_SUFFIXES:
        return suffix
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type == "audio/webm":
        return ".webm"
    if content_type in {"audio/ogg", "application/ogg"}:
        return ".ogg"
    if content_type in {"audio/mp4", "audio/mpeg"}:
        return ".m4a"
    if content_type in {"audio/wav", "audio/x-wav"}:
        return ".wav"
    return ""


async def _idempotent_lookup(room_id: str, line_id: uuid.UUID, key: str | None) -> str | None:
    """Вернуть id ранее принятого тейка, если тот же ключ уже обрабатывали."""
    if not key:
        return None
    from app.core.redis import get_redis

    try:
        return await get_redis().get(f"recording:idem:{room_id}:{line_id}:{key}")
    except Exception:  # noqa: BLE001 — идемпотентность не должна ломать загрузку
        return None


async def _remember_idempotent(room_id: str, line_id: uuid.UUID, key: str | None, rec_id: str) -> None:
    if not key:
        return
    from app.core.redis import get_redis

    try:
        await get_redis().set(f"recording:idem:{room_id}:{line_id}:{key}", rec_id, ex=3600)
    except Exception:  # noqa: BLE001
        pass


@router.post(
    "/{room_id}/lines/{line_id}/recordings",
    response_model=RecordingOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_take(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    line_id: Annotated[uuid.UUID, PathParam()],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    lead_in_ms: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> RecordingOut:
    line = await lines_service.get_line(session, room.id, line_id)

    existing_id = await _idempotent_lookup(room.id, line_id, idempotency_key)
    if existing_id:
        recording = await recordings_service.get_recording(
            session, room.id, line_id, uuid.UUID(existing_id)
        )
        return recording_to_out(recording)

    # Гонка за реплику: чужой захват не перебиваем, свободную — берём себе автоматически,
    # чтобы «записать» работало без отдельного шага «взять». Назначение живёт в таблице
    # assignments (у реплики такого поля нет), поэтому спрашиваем её явно.
    assignment = await assignments_service.get_assignment(session, line_id)
    if assignment is not None and assignment.participant_id != participant.id:
        names = await assignments_service.participant_names(session, room.id)
        raise Conflict(
            "Эту реплику озвучивает другой участник",
            code="line_taken",
            extra={
                "participant_id": str(assignment.participant_id),
                "display_name": names.get(assignment.participant_id),
            },
        )
    if assignment is None:
        await assignments_service.claim(session, room.id, line_id, participant.id)

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type and content_type not in ALLOWED_TYPES:
        raise UnsupportedFormat(
            f"Неизвестный тип аудио: {content_type}. Ожидается запись из браузера (webm/ogg/wav)",
        )
    suffix = _suffix(file)
    if not suffix:
        raise UnsupportedFormat("Не удалось определить формат записи — пришлите webm/ogg/wav")

    raw_relative, size_bytes, _digest = await storage.save_upload_stream(
        file,
        room.id,
        "recordings",
        str(line_id),
        filename=f"take-{uuid.uuid4().hex[:8]}{suffix}",
        max_bytes=settings.record_max_take_bytes,
    )
    raw_path = storage.absolute(raw_relative)

    info = probe.probe(raw_path)
    if not info.has_audio:
        raw_path.unlink(missing_ok=True)
        raise UnsupportedFormat("В загруженном файле нет звука", code="no_audio_in_take")

    # Длительность меряем отдельно: у webm из MediaRecorder нет заголовка Duration, и
    # `info.duration_ms` там 0 — доверять этому числу нельзя (лимит бы не сработал).
    measured_ms = await asyncio.to_thread(probe.audio_duration_ms, raw_path)
    # «Разгон» (первые секунды после нажатия «записать») в лимит реплики не входит: участник
    # пишет реплику плюс разгон, а в реплику попадает уже обрезанная часть.
    allowed_ms = line.duration_ms + lead_in_ms + settings.record_tolerance_ms
    if measured_ms > allowed_ms:
        raw_path.unlink(missing_ok=True)
        raise RecordingTooLong(
            f"Запись длиннее реплики: {measured_ms} мс против {line.duration_ms} мс "
            f"(+ разгон {lead_in_ms} мс, допуск {settings.record_tolerance_ms} мс)",
            extra={"duration_ms": measured_ms, "line_duration_ms": line.duration_ms},
        )

    processed_name = f"{Path(raw_relative).stem}.wav"
    processed_relative = "/".join([*raw_relative.split("/")[:-1], processed_name])
    processed_path = storage.absolute(processed_relative)
    denoise = (room.settings or {}).get("denoise", settings.denoise_strength)
    await asyncio.to_thread(
        ffmpeg.normalize_recording,
        raw_path,
        processed_path,
        line.duration_ms,
        settings.record_loudness_target,
        skip_ms=lead_in_ms,
        denoise=denoise,
    )
    loudness = await asyncio.to_thread(ffmpeg.loudness_lufs, processed_path)

    recording = await recordings_service.create_take(
        session,
        line_id=line_id,
        participant_id=participant.id,
        raw_path=raw_relative,
        processed_path=processed_relative,
        duration_ms=line.duration_ms,
        size_bytes=size_bytes,
        sample_rate=48000,
        channels=1,
        loudness=loudness,
        line_version=line.version,
    )
    await _remember_idempotent(room.id, line_id, idempotency_key, str(recording.id))
    await emit_event(
        session,
        room.id,
        "recording.ready",
        {
            "line_id": str(line_id),
            "recording_id": str(recording.id),
            "take_number": recording.take_number,
        },
    )
    log.info(
        "take_uploaded",
        room_id=room.id,
        line_id=str(line_id),
        take=recording.take_number,
        size_bytes=size_bytes,
        declared_ms=info.duration_ms,
        line_ms=line.duration_ms,
        lead_in_ms=lead_in_ms,
        denoise=denoise,
    )
    return recording_to_out(recording)


@router.get("/{room_id}/lines/{line_id}/recordings", response_model=list[RecordingOut])
async def list_takes(
    room: RoomDep,
    session: SessionDep,
    line_id: Annotated[uuid.UUID, PathParam()],
) -> list[RecordingOut]:
    await lines_service.get_line(session, room.id, line_id)
    rows = await recordings_service.list_takes(session, room.id, line_id)
    return [recording_to_out(row) for row in rows]


@router.post(
    "/{room_id}/lines/{line_id}/recordings/{recording_id}/current",
    response_model=RecordingOut,
)
async def make_current(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    line_id: Annotated[uuid.UUID, PathParam()],
    recording_id: Annotated[uuid.UUID, PathParam()],
) -> RecordingOut:
    await lines_service.get_line(session, room.id, line_id)
    recording = await recordings_service.set_current(session, room.id, line_id, recording_id)
    await emit_event(
        session,
        room.id,
        "recording.ready",
        {"line_id": str(line_id), "recording_id": str(recording.id), "activated": True},
    )
    return recording_to_out(recording)


@router.delete(
    "/{room_id}/lines/{line_id}/recordings/{recording_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_take(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    line_id: Annotated[uuid.UUID, PathParam()],
    recording_id: Annotated[uuid.UUID, PathParam()],
) -> Response:
    await lines_service.get_line(session, room.id, line_id)
    recording = await recordings_service.get_recording(session, room.id, line_id, recording_id)
    for relative in (recording.processed_path, recording.raw_path):
        if relative:
            path = storage.absolute(relative)
            path.unlink(missing_ok=True)
    was_current = recording.is_current
    await session.delete(recording)
    await session.flush()
    if was_current:
        # Актуальным становится предыдущий тейк, если он есть: участник не должен остаться
        # без озвучки из-за удаления последней записи.
        remaining = await recordings_service.list_takes(session, room.id, line_id)
        if remaining:
            remaining[-1].is_current = True
            await session.flush()
    await emit_event(
        session,
        room.id,
        "recording.deleted",
        {"line_id": str(line_id), "recording_id": str(recording_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
