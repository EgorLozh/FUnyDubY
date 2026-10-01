"""Тесты безопасности: id комнаты, токены участников, защита путей."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import Forbidden
from app.core.security import (
    ROOM_ALPHABET,
    ROOM_ID_RE,
    generate_participant_token,
    generate_room_id,
    hash_token,
    room_relative,
    safe_join,
    token_matches,
)


class TestRoomId:
    def test_format_and_alphabet(self):
        room_id = generate_room_id()
        assert len(room_id) == 20
        assert set(room_id) <= set(ROOM_ALPHABET)
        assert ROOM_ID_RE.match(room_id)

    def test_no_ambiguous_characters(self):
        for char in "01oil":
            assert char not in ROOM_ALPHABET

    def test_ids_are_unique(self):
        assert len({generate_room_id() for _ in range(500)}) == 500


class TestParticipantToken:
    def test_token_is_long_and_url_safe(self):
        token = generate_participant_token()
        assert len(token) >= 40
        assert all(char.isalnum() or char in "-_" for char in token)

    def test_hash_is_not_reversible_and_matches(self):
        token = generate_participant_token()
        digest = hash_token(token)
        assert token not in digest
        assert len(digest) == 64
        assert token_matches(token, digest)

    def test_wrong_token_rejected(self):
        digest = hash_token(generate_participant_token())
        assert not token_matches(generate_participant_token(), digest)


class TestSafeJoin:
    def test_normal_path(self, tmp_path: Path):
        assert safe_join(tmp_path, "rooms/abc/original/source.mp4").name == "source.mp4"

    def test_traversal_is_rejected(self, tmp_path: Path):
        for evil in ("../secret", "rooms/../../etc/passwd", "..", "a/../../b"):
            with pytest.raises(Forbidden):
                safe_join(tmp_path, evil)

    def test_absolute_path_is_treated_as_relative_and_stays_inside(self, tmp_path: Path):
        result = safe_join(tmp_path, "/etc/passwd")
        assert str(result).startswith(str(tmp_path))

    def test_windows_separators_are_normalised(self, tmp_path: Path):
        assert safe_join(tmp_path, "rooms\\abc\\file.wav").name == "file.wav"


def test_room_relative_builds_expected_layout():
    assert room_relative("abc123", "speech", "segments", "x.wav") == "rooms/abc123/speech/segments/x.wav"
