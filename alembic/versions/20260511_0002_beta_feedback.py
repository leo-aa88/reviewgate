"""Add ``beta_feedback`` for hosted beta feedback (issue #55).

Revision ID: 16_1_0002
Revises: 16_1_0001
Create Date: 2026-05-11

"""

from __future__ import annotations

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from reviewgate.app.storage.models import TABLE_BETA_FEEDBACK

revision: Final[str] = "16_1_0002"
down_revision: Final[str] = "16_1_0001"
branch_labels: Final[None] = None
depends_on: Final[None] = None


def upgrade() -> None:
    """Create ``beta_feedback``."""

    op.create_table(
        TABLE_BETA_FEEDBACK,
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("contact", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Drop ``beta_feedback``."""

    op.drop_table(TABLE_BETA_FEEDBACK)
