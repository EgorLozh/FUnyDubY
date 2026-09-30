"""Идентификаторы, токены и безопасная работа с путями."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path

from app.core.config import settings
from app.core.errors import Forbidden

# Алфавит base32 без визуально неоднозначных символов: 0/O, 1/I/L
ROOM_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
ROOM_ID_RE = r"^[23456789abcdefghjkmnpqrstuvwxyz]{20}$"


def generate_room_id() -> str:
    """Публичный идентификатор комнаты: ~118 бит энтропии, дружелюбный к диктовке."""
    raw = secrets.token_bytes(settings.room_id_bytes)
    n = int.from_bytes(raw, "big")
    out = []
    for _ in range(settings.room_id_length):
        n, rem = divmod(n, len(ROOM_ALPHABET))
        out.append(ROOM_ALPHABET[rem])
    return "".join(out)


def generate_participant_token() -> str:
    return secrets.token_urlsafe(settings.participant_token_bytes)


def hash_token(token: str) -> str:
    """В БД храним только хеш; соль не нужна — токен и так 256-битный случайный."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, token_hash: str) -> bool:
    """Сравнение в постоянном времени."""
    return hmac.compare_digest(hash_token(token), token_hash)


def room_dir(room_id: str) -> Path:
    return settings.rooms_root / room_id


def safe_join(root: Path, relative: str | Path) -> Path:
    """Склеить корень и относительный путь, отклонив выход за пределы корня.

    Единственная точка, где пользовательские/БД-пути превращаются в файловые.
    """
    rel = str(relative).replace("\\", "/").lstrip("/")
    if rel.startswith("../") or "/../" in f"/{rel}":
        raise Forbidden("Недопустимый путь", code="path_traversal")
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise Forbidden("Недопустимый путь", code="path_traversal")
    return candidate


def room_relative(room_id: str, *parts: str) -> str:
    """Относительный путь внутри комнаты — то, что кладём в БД."""
    return "/".join(("rooms", room_id, *[p.strip("/") for p in parts]))
