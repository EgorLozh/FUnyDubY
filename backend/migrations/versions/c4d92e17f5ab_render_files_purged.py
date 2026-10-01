"""render_jobs.files_purged: файлы старых сборок удалены, история остаётся

Revision ID: c4d92e17f5ab
Revises: b7f31c8d4a02
Create Date: 2026-10-01
"""

from __future__ import annotations

from alembic import op

revision: str = "c4d92e17f5ab"
down_revision: str | None = "b7f31c8d4a02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE render_jobs ADD COLUMN IF NOT EXISTS files_purged boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE render_jobs DROP COLUMN IF EXISTS files_purged")
