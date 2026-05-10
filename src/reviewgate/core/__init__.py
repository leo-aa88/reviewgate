"""Deterministic reviewability engine (`reviewgate-core` boundary per docs/DESIGN.md §4.1)."""

from . import categorizer, cli, config, heuristics, report, schemas
from .config import (
    ConfigLoadResult,
    ConfigMode,
    ReviewGateConfig,
    StatusFailOn,
    load_config,
)
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
    "categorizer",
    "cli",
    "config",
    "heuristics",
    "load_config",
    "report",
    "schemas",
]
