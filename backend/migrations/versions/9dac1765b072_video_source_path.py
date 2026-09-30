"""video source_path

Колонка пути к исходному файлу внутри хранилища. До неё путь собирался из
storage_dir + угаданного имени, что ломается при загрузке в другом формате.

Автогенерация в этом файле была неверной (предлагала повторно создать FK
rooms -> videos/participants, которые уже есть с initial_schema), поэтому
миграция написана руками.

Revision ID: 9dac1765b072
Revises: c6a213b53ed1
Create Date: 2026-09-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9dac1765b072"
down_revision: str | None = "c6a213b53ed1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("source_path", sa.Text(), nullable=True))
    op.execute(
        "UPDATE videos SET source_path = storage_dir || '/source.mp4' WHERE source_path IS NULL"
    )
    op.alter_column("videos", "source_path", nullable=False)
    op.create_index("ix_videos_source_path", "videos", ["source_path"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_videos_source_path", table_name="videos")
    op.drop_column("videos", "source_path")
