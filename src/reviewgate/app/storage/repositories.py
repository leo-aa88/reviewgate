"""Repository helpers for ``analyses`` lifecycle (``docs/DESIGN.md`` §16.1, issue #46).

Workers create or resume ``analyses`` rows at job start, then transition statuses
to terminal ``completed`` or ``failed`` states. The composite uniqueness rule
lives on the ORM model and Alembic migration; callers rely on
:func:`begin_analysis_for_job_start` for idempotent first-writer semantics.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from reviewgate.app.storage.models import Analysis, AnalysisReport

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Worker claimed the row and is executing the pipeline.
ANALYSIS_STATUS_RUNNING: Final[str] = "running"
#: Deterministic (and optional LLM) stages finished successfully.
ANALYSIS_STATUS_COMPLETED: Final[str] = "completed"
#: Terminal failure; ``error_code`` should be set.
ANALYSIS_STATUS_FAILED: Final[str] = "failed"

BeginAnalysisKind = Literal[
    "created",
    "already_completed",
    "already_running",
    "resumed_from_failed",
]


class AnalysisNaturalKey(NamedTuple):
    """Five-part natural key for an ``analyses`` row (§16.1 / §13.7)."""

    repository_id: uuid.UUID
    pull_number: int
    head_sha: str
    config_hash: str
    pr_metadata_hash: str


def parse_analysis_job_natural_key(
    payload: dict[str, object],
) -> AnalysisNaturalKey | None:
    """Extract a natural key from a Dramatiq job payload when all fields exist.

    Optional keys (all required together, otherwise ``None``):

    * ``reviewgate_repository_id`` — UUID string for :attr:`Repository.id`.
    * ``reviewgate_pull_number`` — positive ``int``.
    * ``reviewgate_head_sha`` — non-empty ``str``.
    * ``reviewgate_config_hash`` — non-empty ``str``.
    * ``reviewgate_pr_metadata_hash`` — non-empty ``str``.

    Args:
        payload: Actor envelope merged by the webhook or future pipeline.

    Returns:
        Parsed key, or ``None`` when any field is missing or mistyped.
    """

    raw_repo = payload.get("reviewgate_repository_id")
    raw_pull = payload.get("reviewgate_pull_number")
    raw_head = payload.get("reviewgate_head_sha")
    raw_cfg = payload.get("reviewgate_config_hash")
    raw_meta = payload.get("reviewgate_pr_metadata_hash")

    if isinstance(raw_repo, uuid.UUID):
        repo_id = raw_repo
    elif isinstance(raw_repo, str) and raw_repo.strip():
        try:
            repo_id = uuid.UUID(raw_repo.strip())
        except ValueError:
            return None
    else:
        return None

    if isinstance(raw_pull, bool) or not isinstance(raw_pull, int) or raw_pull < 1:
        return None
    if not isinstance(raw_head, str) or not raw_head.strip():
        return None
    if not isinstance(raw_cfg, str) or not raw_cfg.strip():
        return None
    if not isinstance(raw_meta, str) or not raw_meta.strip():
        return None

    return AnalysisNaturalKey(
        repository_id=repo_id,
        pull_number=raw_pull,
        head_sha=raw_head.strip(),
        config_hash=raw_cfg.strip(),
        pr_metadata_hash=raw_meta.strip(),
    )


def _classify_existing_analysis_row(
    session: "Session",
    row: Analysis,
) -> tuple[uuid.UUID, BeginAnalysisKind]:
    """Return disposition for a row that already exists under the natural key."""

    if row.status == ANALYSIS_STATUS_COMPLETED:
        return row.id, "already_completed"
    if row.status == ANALYSIS_STATUS_RUNNING:
        return row.id, "already_running"
    if row.status == ANALYSIS_STATUS_FAILED:
        row.status = ANALYSIS_STATUS_RUNNING
        row.completed_at = None
        row.error_code = None
        row.reviewability = None
        session.flush()
        return row.id, "resumed_from_failed"

    msg = f"unexpected analyses.status value: {row.status!r}"
    raise ValueError(msg)


def begin_analysis_for_job_start(
    session: "Session",
    key: AnalysisNaturalKey,
) -> tuple[uuid.UUID, BeginAnalysisKind]:
    """Create or observe an ``analyses`` row when a worker job starts.

    Inserts a ``running`` row when absent. When a terminal ``completed`` row
    exists, returns ``already_completed`` so callers can skip duplicate work
    (§13.7 enqueue dedupe). ``failed`` rows reset to ``running`` for retries.
    Concurrent ``running`` rows return ``already_running`` for a second worker
    that lost the race. If two workers race on insert, :exc:`~sqlalchemy.exc.IntegrityError`
    is caught and the winner row is classified the same way as a pre-existing row.

    Args:
        session: Open SQLAlchemy session (caller commits).
        key: Parsed natural key for the analysis attempt.

    Returns:
        ``(analysis_id, disposition)`` describing how the worker should proceed.
    """

    stmt = select(Analysis).where(
        Analysis.repository_id == key.repository_id,
        Analysis.pull_number == key.pull_number,
        Analysis.head_sha == key.head_sha,
        Analysis.config_hash == key.config_hash,
        Analysis.pr_metadata_hash == key.pr_metadata_hash,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        created = Analysis(
            id=uuid.uuid4(),
            repository_id=key.repository_id,
            pull_number=key.pull_number,
            head_sha=key.head_sha,
            config_hash=key.config_hash,
            pr_metadata_hash=key.pr_metadata_hash,
            status=ANALYSIS_STATUS_RUNNING,
            created_at=datetime.now(tz=UTC),
        )
        session.add(created)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            winner = session.execute(stmt).scalar_one_or_none()
            if winner is None:
                msg = "analyses row missing after unique constraint violation"
                raise RuntimeError(msg) from None
            return _classify_existing_analysis_row(session, winner)
        return created.id, "created"

    return _classify_existing_analysis_row(session, row)


def mark_analysis_completed(
    session: "Session",
    analysis_id: uuid.UUID,
    *,
    reviewability: str,
) -> None:
    """Set ``status`` to ``completed`` and stamp ``completed_at`` (UTC)."""

    row = session.get(Analysis, analysis_id)
    if row is None:
        msg = f"analysis id not found: {analysis_id}"
        raise ValueError(msg)
    row.status = ANALYSIS_STATUS_COMPLETED
    row.reviewability = reviewability
    row.completed_at = datetime.now(tz=UTC)
    row.error_code = None


def mark_analysis_failed(
    session: "Session",
    analysis_id: uuid.UUID,
    *,
    error_code: str,
) -> None:
    """Set ``status`` to ``failed`` with a short ``error_code``."""

    row = session.get(Analysis, analysis_id)
    if row is None:
        msg = f"analysis id not found: {analysis_id}"
        raise ValueError(msg)
    if not error_code.strip():
        msg = "error_code must be a non-empty string"
        raise ValueError(msg)
    row.status = ANALYSIS_STATUS_FAILED
    row.error_code = error_code.strip()
    row.completed_at = datetime.now(tz=UTC)
    row.reviewability = None


def update_analysis_pr_size_fields(
    session: "Session",
    analysis_id: uuid.UUID,
    *,
    files_changed: int,
    raw_loc_changed: int,
    human_loc_changed: int,
) -> None:
    """Populate ``analyses`` size columns from deterministic stats (§16.1)."""

    row = session.get(Analysis, analysis_id)
    if row is None:
        msg = f"analysis id not found: {analysis_id}"
        raise ValueError(msg)
    if files_changed < 0 or raw_loc_changed < 0 or human_loc_changed < 0:
        msg = "size fields must be non-negative"
        raise ValueError(msg)
    row.files_changed = files_changed
    row.raw_loc_changed = raw_loc_changed
    row.human_loc_changed = human_loc_changed


def insert_analysis_report(
    session: "Session",
    analysis_id: uuid.UUID,
    *,
    report_json: dict[str, object],
    deterministic_json: dict[str, object],
) -> uuid.UUID:
    """Insert a deterministic ``analysis_reports`` row (issue #50)."""

    report_id = uuid.uuid4()
    session.add(
        AnalysisReport(
            id=report_id,
            analysis_id=analysis_id,
            report_json=report_json,
            deterministic_json=deterministic_json,
            llm_used=False,
            created_at=datetime.now(tz=UTC),
        ),
    )
    return report_id


def completed_analysis_exists_for_key(
    session: "Session",
    key: AnalysisNaturalKey,
) -> bool:
    """Return ``True`` when a terminal ``completed`` row exists for the key (§13.7)."""

    stmt = (
        select(Analysis.id)
        .where(
            Analysis.repository_id == key.repository_id,
            Analysis.pull_number == key.pull_number,
            Analysis.head_sha == key.head_sha,
            Analysis.config_hash == key.config_hash,
            Analysis.pr_metadata_hash == key.pr_metadata_hash,
            Analysis.status == ANALYSIS_STATUS_COMPLETED,
        )
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none() is not None
