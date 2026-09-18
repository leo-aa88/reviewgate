"""Add ``claimed_at`` and ``claim_token`` to ``webhook_deliveries`` (issue #154).

Revision ID: 16_1_0003
Revises: 16_1_0002
Create Date: 2026-05-12

"""

from __future__ import annotations

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from reviewgate.app.storage.models import TABLE_WEBHOOK_DELIVERIES

revision: Final[str] = "16_1_0003"
down_revision: Final[str] = "16_1_0002"
branch_labels: Final[None] = None
depends_on: Final[None] = None


def upgrade() -> None:
    """Add ``claimed_at`` and ``claim_token`` columns with backfill from ``created_at``."""

    op.add_column(
        TABLE_WEBHOOK_DELIVERIES,
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        TABLE_WEBHOOK_DELIVERIES,
        sa.Column(
            "claim_token",
            pg.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # Safely backfill existing rows from created_at so historical deliveries do not receive a fake active lease
    op.execute(
        f"UPDATE {TABLE_WEBHOOK_DELIVERIES} SET claimed_at = created_at WHERE claimed_at IS NULL"
    )

    op.alter_column(
        TABLE_WEBHOOK_DELIVERIES,
        "claimed_at",
        nullable=False,
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    """Drop ``claim_token`` and ``claimed_at`` columns from ``webhook_deliveries``."""

    op.drop_column(TABLE_WEBHOOK_DELIVERIES, "claim_token")
    op.drop_column(TABLE_WEBHOOK_DELIVERIES, "claimed_at")
