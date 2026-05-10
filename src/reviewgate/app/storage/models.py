"""SQLAlchemy ORM models matching ``docs/DESIGN.md`` §16.1.

Defines PostgreSQL-backed tables for GitHub App installations, linked
repositories, per-PR analysis rows, persisted JSON reports, beta lead
capture, and webhook delivery deduplication. Column names, types, nullability,
uniques, and indexes follow the design document; Alembic revisions under
``alembic/versions`` must stay aligned with this module.

Example:
    Inspecting registered table names (requires optional ``app`` extras)::

        from reviewgate.app.storage.models import Base

        assert "installations" in Base.metadata.tables
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# --- Identifier constants (shared with Alembic revisions) --------------------

INDEX_ANALYSES_CREATED_AT: Final[str] = "idx_analyses_created_at"
INDEX_ANALYSES_REPO_PR: Final[str] = "idx_analyses_repo_pr"
INDEX_WEBHOOK_DELIVERIES_CREATED_AT: Final[str] = "idx_webhook_deliveries_created_at"
TABLE_ANALYSES: Final[str] = "analyses"
TABLE_ANALYSIS_REPORTS: Final[str] = "analysis_reports"
TABLE_BETA_LEADS: Final[str] = "beta_leads"
TABLE_INSTALLATIONS: Final[str] = "installations"
TABLE_REPOSITORIES: Final[str] = "repositories"
TABLE_WEBHOOK_DELIVERIES: Final[str] = "webhook_deliveries"
UQ_ANALYSES_NATURAL_KEY: Final[str] = (
    "uq_analyses_repository_pull_head_config_pr_metadata_hash"
)


class Base(DeclarativeBase):
    """Declarative base for ReviewGate hosted-app ORM models."""


class Installation(Base):
    """GitHub App installation row (§16.1 ``installations``).

    Attributes:
        id: Surrogate primary key.
        github_installation_id: Numeric installation id from GitHub (unique).
        account_login: Owning account login (user or organization).
        account_type: GitHub account type string (for example ``User`` or
            ``Organization``).
        created_at: Row creation timestamp (UTC).
        deleted_at: Soft-delete timestamp when the installation is removed.
    """

    __tablename__ = TABLE_INSTALLATIONS

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    github_installation_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
    )
    account_login: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class Repository(Base):
    """Repository enabled for an installation (§16.1 ``repositories``).

    Attributes:
        id: Surrogate primary key.
        installation_id: Parent installation.
        github_repository_id: Numeric repository id from GitHub (unique).
        owner: Repository owner login.
        name: Short repository name (without owner prefix).
        full_name: ``owner/name`` slug.
        private: Whether the repository is private on GitHub.
        active: Whether ReviewGate should process webhooks for this repo.
        created_at: Row creation timestamp (UTC).
    """

    __tablename__ = TABLE_REPOSITORIES

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{TABLE_INSTALLATIONS}.id"),
        nullable=False,
    )
    github_repository_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
    )
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    private: Mapped[bool] = mapped_column(Boolean, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class Analysis(Base):
    """Single PR analysis attempt keyed by head SHA and metadata hash (§16.1).

    The composite unique key matches the design document so repeated webhooks
    for the same logical inputs dedupe cleanly at the database layer.

    Attributes:
        id: Surrogate primary key.
        repository_id: Repository under analysis.
        pull_number: GitHub pull request number.
        head_sha: Commit SHA for the PR head ref at analysis time.
        config_hash: Hash of effective ``.reviewgate.yml`` (or default) config.
        pr_metadata_hash: Hash of normalized PR title/body/issue refs, etc.
        status: Worker lifecycle status (application-defined string).
        reviewability: Deterministic or final PASS/WARN/FAIL when completed.
        check_run_id: GitHub check run id when a check was created.
        check_run_name: Configured check name (for example
            ``reviewgate/reviewability``).
        files_changed: Count of changed files from GitHub, if known.
        raw_loc_changed: Raw lines added plus deleted across files.
        human_loc_changed: Human-authored LOC after exclusions.
        created_at: Row creation timestamp (UTC).
        completed_at: Worker completion timestamp, if finished.
        error_code: Short machine-readable error when status is failed.
    """

    __tablename__ = TABLE_ANALYSES
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "pull_number",
            "head_sha",
            "config_hash",
            "pr_metadata_hash",
            name=UQ_ANALYSES_NATURAL_KEY,
        ),
        Index(INDEX_ANALYSES_REPO_PR, "repository_id", "pull_number"),
        Index(INDEX_ANALYSES_CREATED_AT, "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{TABLE_REPOSITORIES}.id"),
        nullable=False,
    )
    pull_number: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    pr_metadata_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reviewability: Mapped[str | None] = mapped_column(Text, nullable=True)
    check_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    check_run_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    files_changed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_loc_changed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    human_loc_changed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnalysisReport(Base):
    """Persisted deterministic and optional LLM report payloads (§16.1).

    Attributes:
        id: Surrogate primary key.
        analysis_id: Parent analysis row.
        report_json: Final merged report JSON (hosted shape).
        deterministic_json: Deterministic engine output JSON.
        llm_used: Whether an LLM stage contributed to ``report_json``.
        llm_provider: Provider slug when ``llm_used`` is true.
        input_tokens: LLM input token count when measured.
        output_tokens: LLM output token count when measured.
        estimated_cost_usd: Estimated spend in USD for the LLM call.
        created_at: Row creation timestamp (UTC).
    """

    __tablename__ = TABLE_ANALYSIS_REPORTS

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{TABLE_ANALYSES}.id"),
        nullable=False,
    )
    report_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    deterministic_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    llm_used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    llm_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class BetaLead(Base):
    """Marketing beta signup row (§16.1 ``beta_leads``).

    Attributes:
        id: Surrogate primary key.
        email: Submitter email (required).
        name: Optional display name.
        company: Optional company name.
        role: Optional job role string.
        github_org: Optional GitHub organization slug.
        team_size: Optional team size bucket string.
        source: Optional attribution source (for example ``landing``).
        created_at: Row creation timestamp (UTC).
    """

    __tablename__ = TABLE_BETA_LEADS

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    company: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_org: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class WebhookDelivery(Base):
    """GitHub webhook delivery dedupe row (§16.1 ``webhook_deliveries``).

    Attributes:
        id: Surrogate primary key.
        github_delivery_id: Value of ``X-GitHub-Delivery`` (unique).
        event_name: Webhook event type (for example ``pull_request``).
        processed: Whether the worker finished handling this delivery.
        created_at: Row creation timestamp (UTC).
    """

    __tablename__ = TABLE_WEBHOOK_DELIVERIES
    __table_args__ = (Index(INDEX_WEBHOOK_DELIVERIES_CREATED_AT, "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    github_delivery_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    processed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
