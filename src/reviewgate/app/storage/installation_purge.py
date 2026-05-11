"""Purge persisted analyses for long-uninstalled GitHub App installations (§23.1).

When ``installations.deleted_at`` is older than the retention window, delete
``analysis_reports`` rows and their parent ``analyses`` for repositories that
belong to those installations. Repository and installation rows are retained so
operators keep a record of historical installs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from reviewgate.app.storage.models import Analysis, AnalysisReport, Installation, Repository

_DEFAULT_RETENTION_DAYS: Final[int] = 30


def purge_analyses_for_uninstalled_installations(
    session: Session,
    *,
    days: int = _DEFAULT_RETENTION_DAYS,
) -> tuple[int, int]:
    """Delete analyses and reports for installs uninstalled longer than ``days``.

    Args:
        session: Open ORM session (caller commits).
        days: Only rows tied to installations with ``deleted_at`` strictly
            before ``now(UTC) - days`` are removed.

    Returns:
        ``(reports_deleted, analyses_deleted)`` row counts from the ORM driver.
    """

    cutoff = datetime.now(UTC) - timedelta(days=days)
    repo_ids = list(
        session.scalars(
            select(Repository.id).join(
                Installation,
                Repository.installation_id == Installation.id,
            ).where(
                Installation.deleted_at.is_not(None),
                Installation.deleted_at < cutoff,
            ),
        ).all(),
    )
    if not repo_ids:
        return 0, 0

    analysis_ids = list(
        session.scalars(select(Analysis.id).where(Analysis.repository_id.in_(repo_ids))).all(),
    )
    if not analysis_ids:
        return 0, 0

    reports_result = session.execute(
        delete(AnalysisReport).where(AnalysisReport.analysis_id.in_(analysis_ids)),
    )
    analyses_result = session.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
    return int(reports_result.rowcount or 0), int(analyses_result.rowcount or 0)


__all__ = ["purge_analyses_for_uninstalled_installations"]
