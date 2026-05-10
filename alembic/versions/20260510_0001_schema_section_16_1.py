"""Initial persistence schema (``docs/DESIGN.md`` §16.1).

Creates ``installations``, ``repositories``, ``analyses``, ``analysis_reports``,
``beta_leads``, and ``webhook_deliveries`` with the composite unique key and
indexes specified in the design document.

Primary-key defaults use ``gen_random_uuid()``. The migration enables
``pgcrypto`` with ``CREATE EXTENSION IF NOT EXISTS`` so the same revision stays
executable on PostgreSQL clusters where that function is only available through
the extension (older versions / stripped images), while remaining a no-op when
the function is already built-in (PostgreSQL 13+).

Revision ID: 16_1_0001
Revises:
Create Date: 2026-05-10

"""

from __future__ import annotations

from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from reviewgate.app.storage.models import (
    INDEX_ANALYSES_CREATED_AT,
    INDEX_ANALYSES_REPO_PR,
    INDEX_WEBHOOK_DELIVERIES_CREATED_AT,
    TABLE_ANALYSES,
    TABLE_ANALYSIS_REPORTS,
    TABLE_BETA_LEADS,
    TABLE_INSTALLATIONS,
    TABLE_REPOSITORIES,
    TABLE_WEBHOOK_DELIVERIES,
    UQ_ANALYSES_NATURAL_KEY,
)

revision: Final[str] = "16_1_0001"
down_revision: Final[str | None] = None
branch_labels: Final[None] = None
depends_on: Final[None] = None

_EXTENSION_PGCRYPTO: Final[str] = "pgcrypto"


def upgrade() -> None:
    """Create §16.1 tables, constraints, and indexes."""

    op.execute(sa.text(f'CREATE EXTENSION IF NOT EXISTS "{_EXTENSION_PGCRYPTO}"'))

    op.create_table(
        TABLE_INSTALLATIONS,
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_installation_id"),
    )
    op.create_table(
        TABLE_REPOSITORIES,
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("installation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("github_repository_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("private", sa.Boolean(), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            [f"{TABLE_INSTALLATIONS}.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_repository_id"),
    )
    op.create_table(
        TABLE_ANALYSES,
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("repository_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("pull_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("pr_metadata_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reviewability", sa.Text(), nullable=True),
        sa.Column("check_run_id", sa.BigInteger(), nullable=True),
        sa.Column("check_run_name", sa.Text(), nullable=True),
        sa.Column("files_changed", sa.Integer(), nullable=True),
        sa.Column("raw_loc_changed", sa.Integer(), nullable=True),
        sa.Column("human_loc_changed", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            [f"{TABLE_REPOSITORIES}.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id",
            "pull_number",
            "head_sha",
            "config_hash",
            "pr_metadata_hash",
            name=UQ_ANALYSES_NATURAL_KEY,
        ),
    )
    op.create_index(
        INDEX_ANALYSES_REPO_PR,
        TABLE_ANALYSES,
        ["repository_id", "pull_number"],
        unique=False,
    )
    op.create_index(
        INDEX_ANALYSES_CREATED_AT,
        TABLE_ANALYSES,
        ["created_at"],
        unique=False,
    )
    op.create_table(
        TABLE_ANALYSIS_REPORTS,
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("analysis_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("report_json", pg.JSONB(), nullable=False),
        sa.Column("deterministic_json", pg.JSONB(), nullable=False),
        sa.Column(
            "llm_used",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("llm_provider", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            [f"{TABLE_ANALYSES}.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        TABLE_BETA_LEADS,
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("github_org", sa.Text(), nullable=True),
        sa.Column("team_size", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        TABLE_WEBHOOK_DELIVERIES,
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("github_delivery_id", sa.Text(), nullable=False),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column(
            "processed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_delivery_id"),
    )
    op.create_index(
        INDEX_WEBHOOK_DELIVERIES_CREATED_AT,
        TABLE_WEBHOOK_DELIVERIES,
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop §16.1 objects in dependency-safe order."""

    op.drop_index(
        INDEX_WEBHOOK_DELIVERIES_CREATED_AT,
        table_name=TABLE_WEBHOOK_DELIVERIES,
    )
    op.drop_table(TABLE_WEBHOOK_DELIVERIES)
    op.drop_table(TABLE_BETA_LEADS)
    op.drop_table(TABLE_ANALYSIS_REPORTS)
    op.drop_index(INDEX_ANALYSES_CREATED_AT, table_name=TABLE_ANALYSES)
    op.drop_index(INDEX_ANALYSES_REPO_PR, table_name=TABLE_ANALYSES)
    op.drop_table(TABLE_ANALYSES)
    op.drop_table(TABLE_REPOSITORIES)
    op.drop_table(TABLE_INSTALLATIONS)
