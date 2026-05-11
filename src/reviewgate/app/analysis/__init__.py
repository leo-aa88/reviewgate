"""Analysis pipeline package for the hosted GitHub App (``docs/DESIGN.md`` §15).

Caches, locks, and worker orchestration build on deterministic inputs from
``reviewgate.core`` while owning all network and Redis side effects here.
"""

from __future__ import annotations

from reviewgate.app.analysis.cache import (
    analysis_cache_key,
    worker_job_lock_key,
)
from reviewgate.app.analysis.config_hash import (
    compute_config_hash_from_yaml,
    fetch_reviewgate_yml_and_config_hash,
)
from reviewgate.app.analysis.pr_file_tiers import (
    HUGE_PR_FAIL_FAST_MESSAGE,
    PrFileTierClassification,
    classify_changed_file_count,
)
from reviewgate.app.analysis.pr_metadata_hash import (
    build_pr_metadata_hash_payload,
    compute_pr_metadata_hash,
    normalize_text_for_pr_metadata_hash,
)
from reviewgate.app.analysis.result_cache import (
    ANALYSIS_RESULT_CACHE_TTL_SECONDS,
    get_cached_final_report,
    set_cached_final_report,
)

__all__ = [
    "ANALYSIS_RESULT_CACHE_TTL_SECONDS",
    "HUGE_PR_FAIL_FAST_MESSAGE",
    "PrFileTierClassification",
    "analysis_cache_key",
    "build_pr_metadata_hash_payload",
    "classify_changed_file_count",
    "compute_config_hash_from_yaml",
    "compute_pr_metadata_hash",
    "fetch_reviewgate_yml_and_config_hash",
    "get_cached_final_report",
    "normalize_text_for_pr_metadata_hash",
    "set_cached_final_report",
    "worker_job_lock_key",
]
