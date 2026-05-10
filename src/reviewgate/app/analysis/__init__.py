"""Analysis pipeline package for the hosted GitHub App (``docs/DESIGN.md`` §15).

Caches, locks, and worker orchestration build on deterministic inputs from
``reviewgate.core`` while owning all network and Redis side effects here.
"""

from __future__ import annotations

from reviewgate.app.analysis.cache import (
    analysis_cache_key,
    worker_job_lock_key,
)
from reviewgate.app.analysis.pr_file_tiers import (
    HUGE_PR_FAIL_FAST_MESSAGE,
    PrFileTierClassification,
    classify_changed_file_count,
)

__all__ = [
    "HUGE_PR_FAIL_FAST_MESSAGE",
    "PrFileTierClassification",
    "analysis_cache_key",
    "classify_changed_file_count",
    "worker_job_lock_key",
]
