"""Конфигурация приложения: всё из окружения, ничего хардкодом."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- приложение ---
    app_env: str = "development"
    app_secret: str = "dev-secret-change-me"
    public_base_url: str = "http://localhost:8090"
    log_level: str = "INFO"
    log_format: str = "json"  # json | console

    # --- PostgreSQL (вне compose) ---
    database_url: str = "postgresql+asyncpg://dubbing:dubbing@localhost:5432/dubbing"
    db_pool_size: int = 10
    db_pool_max_overflow: int = 5
    db_echo: bool = False

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- хранилище ---
    storage_root: Path = Path("/data")
    max_upload_mb: int = 2048
    max_video_minutes: int = 10
    max_room_bytes: int = 6_442_450_944
    min_free_disk_mb: int = 2048
    room_ttl_days: int = 7
    global_max_active_rooms: int = 50

    # --- комната / участник ---
    room_id_bytes: int = 16
    room_id_length: int = 20
    participant_token_bytes: int = 32
    assignment_ttl_minutes: int = 15
    assignment_heartbeat_grace_s: int = 60

    # Обработка не должна зависать, если воркер для очереди не поднят
    job_stage_stall_minutes: int = 20
    maintenance_interval_s: int = 120

    # --- запись ---
    record_tolerance_ms: int = 250
    record_loudness_target: int = -16
    record_max_take_mb: int = 25

    # --- диалог ---
    merge_gap_ms: int = 350
    sentence_gap_ms: int = 150
    min_line_ms: int = 600
    max_line_ms: int = 12000
    short_line_ms: int = 400
    segment_pad_ms: int = 80

    # --- ML ---
    separation_model: str = "bandit_v2"
    separation_mode: str = "subtract_and_duck"
    separation_chunk_s: int = 25
    separation_overlap_s: int = 1
    default_ducking_db: int = -7
    # Bandit отдаёт готовые стемы, поэтому дуккинг ему не нужен: фон и так без речи.
    bandit_ducking_db: int = 0
    # Путь к чекпоинту Bandit (Zenodo record 12701995, checkpoint-multi.ckpt, CC-BY-SA-4.0).
    bandit_weights_path: str = "/data/models/bandit/checkpoint-multi.ckpt"
    stt_backend: str = "whisper"
    stt_model: str = "large-v3"
    stt_fallback_model: str = "large-v3-turbo"
    stt_language: str = ""
    alignment_model: str = "WAV2VEC2_ASR_BASE_960H"
    diarization_model: str = "pyannote/speaker-diarization-community-1"
    diarization_fallback_model: str = "nvidia/diar_streaming_sortformer_4spk-v2.1"
    max_speakers_default: int = 0
    model_cache_dir: Path = Path("/root/.cache/huggingface")
    hf_token: str = Field(default="", repr=False)
    hf_home: Path = Path("/root/.cache/huggingface")

    # --- GPU / ffmpeg ---
    gpu_device: int = 0
    gpu_job_concurrency: int = 1
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ffmpeg_timeout_s: int = 900

    # --- рендер ---
    render_loudness_target: int = -16
    # Сколько ПРОШЛЫХ сборок комнаты держат файлы на диске (0 — только актуальная).
    # Сборка 10-минутного ролика занимает сотни мегабайт, держать историю файлов незачем;
    # строки истории в БД остаются, download по ним отвечает понятной ошибкой.
    render_keep_files: int = 0
    # Токен админки: пусто — админка выключена (маршруты отвечают admin_disabled)
    admin_token: str = ""
    unrecorded_policy: str = "silent"

    # --- прочее ---
    enable_prometheus: bool = False
    sentry_dsn: str = ""

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_video_ms(self) -> int:
        return self.max_video_minutes * 60 * 1000

    @property
    def min_free_disk_bytes(self) -> int:
        return self.min_free_disk_mb * 1024 * 1024

    @property
    def record_max_take_bytes(self) -> int:
        return self.record_max_take_mb * 1024 * 1024

    @property
    def rooms_root(self) -> Path:
        return self.storage_root / "rooms"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
