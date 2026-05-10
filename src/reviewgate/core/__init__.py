"""Deterministic reviewability engine (`reviewgate-core` boundary per docs/DESIGN.md §4.1)."""

from . import (
    aggregate,
    categorizer,
    cli,
    config,
    engine,
    heuristics,
    paths,
    report,
    schemas,
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
from .paths import PathMatcher, match_any
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
    "aggregate",
    "analyze",
    "baseline_reviewability",
    "categorize_changed_files",
    "categorizer",
    "cli",
    "config",
    "engine",
    "heuristics",
    "load_config",
    "match_any",
    "paths",
    "report",
    "schemas",
]
