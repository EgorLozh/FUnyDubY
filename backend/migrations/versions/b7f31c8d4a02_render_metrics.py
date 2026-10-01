"""render_jobs.metrics: метрики сборки (тейки, громкость, потолок)

Добавляет колонку идемпотентно (`IF NOT EXISTS`): на уже обновлённой базе миграция
не падает, а состояние схемы остаётся тем же.

Revision ID: b7f31c8d4a02
Revises: 9dac1765b072
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7f31c8d4a02"
down_revision: str | None = "9dac1765b072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE render_jobs ADD COLUMN IF NOT EXISTS metrics jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE render_jobs DROP COLUMN IF EXISTS metrics")
