"""Deterministic reviewability engine (`reviewgate-core` boundary per docs/DESIGN.md §4.1)."""

from . import (
    aggregate,
    categorizer,
    cli,
    config,
    engine,
    heuristics,
    linked_issue,
    paths,
    pr_body,
    report,
    schemas,
    size,
)
from .aggregate import baseline_reviewability
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
from .paths import PathMatcher, match_any
from .pr_body import weak_body_warning
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
    "ChangedFile",
    "ConfigLoadResult",
    "ConfigMode",
    "EngineInput",
    "EngineWarning",
    "FileCategory",
    "FileCategoryRow",
    "FileStatus",
    "PRRecord",
    "Reviewability",
    "ReviewGateConfig",
    "ReviewabilityReport",
    "SplitHint",
    "StatusFailOn",
    "WarningSeverity",
    "Categorizer",
    "PathMatcher",
    "SizeStats",
    "aggregate",
    "analyze",
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
    "paths",
    "pr_body",
    "report",
    "schemas",
    "size",
    "size_warnings",
    "weak_body_warning",
]
