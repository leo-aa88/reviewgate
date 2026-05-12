"""Deterministic reviewability engine (`reviewgate-core` boundary per docs/DESIGN.md \u00a74.1).

Public API surface (re-exported from this package root):

* Engine entry point: :func:`analyze` (\u00a710.2).
* Aggregator: :func:`baseline_reviewability` (\u00a710.13).
* Heuristics: :func:`categorize_changed_files`, :func:`compute_size_stats`,
  :func:`size_warnings`, :func:`weak_body_warning`,
  :func:`linked_issue_warning`, :func:`risky_paths_warning`,
  :func:`mixed_concern_warning`.
* Report assembly: :func:`suggested_labels` (\u00a713.9 + \u00a712) -- maps the
  engine verdict and warning codes to the user-configurable label list.
  Added on the issue-15 path; consumers that want only the verdict can
  ignore it, but downstream label appliers (the GitHub App from #52,
  the hosted worker from #50) should import it from here rather than
  reaching into :mod:`reviewgate.core.report` directly.
* Config loader: :func:`load_config` and the :class:`ReviewGateConfig`
  / :class:`ConfigLoadResult` Pydantic models.
* Schemas: :class:`EngineInput`, :class:`EngineWarning`,
  :class:`ReviewabilityReport`, :class:`Reviewability`,
  :class:`WarningSeverity`, :class:`FileCategory`, etc.

Subpackages such as :mod:`reviewgate.core.automation_pr` that **export
symbols listed in** :data:`__all__` at the package root are part of the
stable consumer surface (for example :data:`PrAuthorKind` for typed
``stats["pr_author_kind"]`` parsing).

Anything else not re-exported below may move without a deprecation cycle.
"""

from . import (
    aggregate,
    automation_pr,
    categorizer,
    cli,
    config,
    engine,
    heuristics,
    linked_issue,
    mixed_concern,
    paths,
    pr_body,
    report,
    risky_paths,
    schemas,
    size,
)
from .aggregate import baseline_reviewability
from .automation_pr import (
    AUTOMATION_STATS_KEYS,
    KNOWN_CODING_AGENT_AUTOMATION_LOGINS,
    KNOWN_DEPENDENCY_AUTOMATION_LOGINS,
    PrAuthorKind,
    classify_pr_author_login,
    finalize_size_stats_for_pr_author,
    is_known_dependency_automation_login,
    is_manifest_only_dependency_automation_pr,
)
from .categorizer import Categorizer, categorize_changed_files
from .config import (
    ConfigLoadResult,
    ConfigMode,
    ReviewGateConfig,
    StatusFailOn,
    load_config,
)
from .engine import analyze
from .linked_issue import find_issue_references, linked_issue_warning
from .mixed_concern import mixed_concern_warning
from .paths import PathMatcher, match_any
from .pr_body import weak_body_warning
from .report import suggested_labels
from .risky_paths import risky_paths_warning
from .size import SizeStats, compute_size_stats, size_warnings
from .schemas import (
    ChangedFile,
    EngineInput,
    EngineWarning,
    FileCategory,
    FileCategoryRow,
    FileStatus,
    PRRecord,
    Reviewability,
    ReviewabilityReport,
    SplitHint,
    WarningSeverity,
)

__all__ = [
    "AUTOMATION_STATS_KEYS",
    "ChangedFile",
    "ConfigLoadResult",
    "ConfigMode",
    "EngineInput",
    "EngineWarning",
    "FileCategory",
    "FileCategoryRow",
    "FileStatus",
    "KNOWN_CODING_AGENT_AUTOMATION_LOGINS",
    "KNOWN_DEPENDENCY_AUTOMATION_LOGINS",
    "PRRecord",
    "PrAuthorKind",
    "Reviewability",
    "ReviewGateConfig",
    "ReviewabilityReport",
    "SplitHint",
    "StatusFailOn",
    "WarningSeverity",
    "Categorizer",
    "classify_pr_author_login",
    "finalize_size_stats_for_pr_author",
    "is_known_dependency_automation_login",
    "is_manifest_only_dependency_automation_pr",
    "PathMatcher",
    "SizeStats",
    "aggregate",
    "analyze",
    "automation_pr",
    "baseline_reviewability",
    "categorize_changed_files",
    "categorizer",
    "cli",
    "compute_size_stats",
    "config",
    "engine",
    "find_issue_references",
    "heuristics",
    "linked_issue",
    "linked_issue_warning",
    "load_config",
    "match_any",
    "mixed_concern",
    "mixed_concern_warning",
    "paths",
    "pr_body",
    "report",
    "risky_paths",
    "risky_paths_warning",
    "schemas",
    "size",
    "size_warnings",
    "suggested_labels",
    "weak_body_warning",
]
