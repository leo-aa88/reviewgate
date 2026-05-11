"""`.reviewgate.yml` configuration models, defaults, and YAML loader.

Implements docs/DESIGN.md §12 (`.reviewgate.yml` schema and malformed-config
recovery), with default values sourced from §10.3 (thresholds), §10.6
(risky paths), §13.10 (status check), and §21.3 (`llm_reports` default).

Loading is pure: the caller supplies the YAML text. The hosted fetch path
(GitHub base-ref read) is intentionally implemented elsewhere so this module
stays I/O-free per the `reviewgate-core` boundary in §4.1 / §19.
"""

from __future__ import annotations

from typing import Final, Literal

import yaml
from pydantic import ConfigDict, Field, ValidationError

from reviewgate.core._base import StrictModel
from reviewgate.core.schemas import EngineWarning

DEFAULT_CONFIG_PATH: Final[str] = ".reviewgate.yml"
"""Default repo-relative path for the configuration file (§12)."""

CONFIG_WARNING_CODE: Final[str] = "config_invalid"
"""Stable warning code emitted when `.reviewgate.yml` cannot be parsed (§12)."""

CONFIG_WARNING_MESSAGE_TEMPLATE: Final[str] = (
    "ReviewGate could not parse {path}: {error}. Running with defaults."
)
"""Verbatim warning template from §12 (`Malformed config behavior`)."""

CURRENT_CONFIG_VERSION: Final[int] = 1
"""Only `version: 1` is recognized for the MVP (§12)."""

ConfigMode = Literal["app", "action", "both"]
"""`mode` enum from §12 (app | action | both)."""

StatusFailOn = Literal["PASS", "WARN", "FAIL"]
"""`status_check.fail_on` enum aligned with §10.2 reviewability literals."""

ConfigVersion = Literal[1]
"""Strict version literal so unknown versions trigger malformed-config recovery."""

DEFAULT_RISKY_PATHS: Final[tuple[str, ...]] = (
    "**/migrations/**",
    "**/migration/**",
    "**/auth/**",
    "**/authentication/**",
    "**/billing/**",
    "**/payments/**",
    "**/infra/**",
    "**/terraform/**",
    "**/.github/workflows/**",
    "Dockerfile",
    "docker-compose.yml",
    "compose.yml",
)
"""Risky-path globs from §10.6; users may override via `risky_paths`."""

DEFAULT_IGNORED_PATHS: Final[tuple[str, ...]] = ()
"""No ignored paths by default; the §10.8 generated/snapshot/vendored sets are
applied by the categorizer regardless of this user-facing list."""

# §10.7 dependency / lockfile patterns. Engine internals: applied by the
# categorizer (#9) for the ``dependency`` / ``lockfile`` labels and for
# dependency-edit count warnings; lockfile rows feed §10.4 exclusions.
# Dependency automation overrides live in ``automation_pr`` (§10.4.1).
DEFAULT_DEPENDENCY_FILES: Final[tuple[str, ...]] = (
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "go.mod",
    "Cargo.toml",
)
"""Dependency-manifest patterns from §10.7."""

DEFAULT_LOCKFILES: Final[tuple[str, ...]] = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
    "go.sum",
    "Cargo.lock",
)
"""Lockfile patterns from §10.7."""

# §10.8 generated / vendored / minified / snapshot patterns. Engine
# internals: subtracted from human-authored LOC per §10.4 and used by the
# categorizer (#9) for the matching `FileCategory` labels.
DEFAULT_GENERATED_PATHS: Final[tuple[str, ...]] = (
    "**/generated/**",
    "**/gen/**",
    "**/*.pb.go",
    "**/*.generated.*",
    "**/openapi.generated.*",
)
"""Generated-file patterns from §10.8."""

DEFAULT_VENDORED_PATHS: Final[tuple[str, ...]] = (
    "vendor/**",
    "third_party/**",
    "node_modules/**",
)
"""Vendored-dependency patterns from §10.8."""

DEFAULT_MINIFIED_PATHS: Final[tuple[str, ...]] = (
    "**/*.min.js",
    "**/*.min.css",
)
"""Minified-asset patterns from §10.8."""

DEFAULT_SNAPSHOT_PATHS: Final[tuple[str, ...]] = (
    "**/__snapshots__/**",
    "**/*.snap",
)
"""Snapshot-fixture patterns from §10.8."""

# §10.9 test-path patterns. Used by the categorizer (#9) for the `test`
# `FileCategory` and as input to mixed-concern heuristics (#14).
DEFAULT_TEST_PATHS: Final[tuple[str, ...]] = (
    "**/test/**",
    "**/tests/**",
    "**/__tests__/**",
    "*.test.*",
    "*.spec.*",
    "test_*.py",
    "*_test.go",
)
"""Test-path patterns from §10.9."""

DEFAULT_STATUS_CHECK_NAME: Final[str] = "reviewgate/reviewability"
"""Default Checks API name (§13.10); stable unless the repo overrides it."""


class WarnThresholds(StrictModel):
    """`thresholds.warn` block (§10.3, §12)."""

    files_changed: int = Field(
        default=25, ge=0, description="Warn when changed files exceed this count (§10.3)."
    )
    human_loc_changed: int = Field(
        default=800,
        ge=0,
        description="Warn when human_loc_changed exceeds this (§10.3, §10.4 post-exclusion).",
    )
    risky_files_changed: int = Field(
        default=2,
        ge=0,
        description=(
            "Warn when risky-path file count is at or above this (§10.3). "
            "Default 2 avoids a redundant warning on single-file risky edits that "
            "already flow through the risky-path rationale heuristic."
        ),
    )
    dependency_files_changed: int = Field(
        default=1, ge=0, description="Warn when dependency-file edits reach this count (§10.3)."
    )
    config_files_changed: int = Field(
        default=1, ge=0, description="Warn when config-file edits reach this count (§10.3)."
    )


class FailThresholds(StrictModel):
    """`thresholds.fail` block (§10.3, §12)."""

    files_changed: int = Field(
        default=75, ge=0, description="Fail when changed files exceed this count (§10.3)."
    )
    human_loc_changed: int = Field(
        default=2500,
        ge=0,
        description="Fail when human_loc_changed exceeds this (§10.3, §10.4 post-exclusion).",
    )
    risky_files_without_context: int = Field(
        default=1,
        ge=0,
        description="Fail when this many risky files are touched without rationale (§10.3, §10.10).",
    )


class Thresholds(StrictModel):
    """`thresholds` block (§10.3, §12)."""

    warn: WarnThresholds = Field(
        default_factory=WarnThresholds,
        description="Warn thresholds; missing keys fall back to §10.3 defaults.",
    )
    fail: FailThresholds = Field(
        default_factory=FailThresholds,
        description="Fail thresholds; missing keys fall back to §10.3 defaults.",
    )


class Policy(StrictModel):
    """`policy` block (§12)."""

    require_linked_issue: bool = Field(
        default=True, description="Treat missing linked issue as a deterministic warning (§10.10)."
    )
    require_human_summary: bool = Field(
        default=True,
        description="Treat missing human PR summary as a deterministic warning (§10.10).",
    )
    fail_on_risky_paths_without_context: bool = Field(
        default=True, description="Escalate risky-path edits with no rationale to FAIL (§10.10)."
    )
    fail_on_huge_pr: bool = Field(
        default=True, description="Escalate huge-PR tier (§10.3 fail thresholds) to FAIL."
    )
    warn_blocks_merge: bool = Field(
        default=False,
        description="If true, treat WARN as merge-blocking; mirrored on `status_check` (§13.10).",
    )


class Labels(StrictModel):
    """`labels` block (§12, §13.9).

    The `pass` key collides with the Python keyword, so it is exposed as
    `pass_` on the model and aliased to `pass` for YAML/JSON I/O. Keys such as
    ``needs-tests`` use the same ``populate_by_name`` pattern as ``pass``.
    Strict defaults (`extra="forbid"`, `strict=True`, `str_strip_whitespace=True`)
    are inherited verbatim from :class:`StrictModel`; only
    `populate_by_name` is layered on so that Python construction via
    `Labels(pass_="...")` keeps working alongside the `pass` alias.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    pass_: str = Field(
        default="reviewability-pass",
        alias="pass",
        description="Applied when reviewability is PASS (§13.9).",
    )
    warn: str = Field(default="reviewability-warn", description="Applied when WARN (§13.9).")
    fail: str = Field(default="reviewability-fail", description="Applied when FAIL (§13.9).")
    too_large: str = Field(default="too-large", description="Applied for size warnings (§13.9).")
    missing_context: str = Field(
        default="missing-context", description="Applied for missing rationale (§13.9)."
    )
    risky_change: str = Field(
        default="risky-change", description="Applied for risky-path warnings (§13.9)."
    )
    needs_split: str = Field(
        default="needs-split", description="Applied when split hints are emitted (§13.9)."
    )
    needs_tests: str = Field(
        default="needs-tests",
        alias="needs-tests",
        description="Applied when source changes lack test files (§13.9).",
    )
    dependency_change: str = Field(
        default="dependency-change",
        alias="dependency-change",
        description="Applied for dependency-manifest churn (§13.9).",
    )
    config_change: str = Field(
        default="config-change",
        alias="config-change",
        description="Applied for broad config edits (§13.9).",
    )


class StatusCheck(StrictModel):
    """`status_check` block (§12, §13.10)."""

    enabled: bool = Field(
        default=True, description="If false, skip Checks API publication (§13.10)."
    )
    name: str = Field(
        default=DEFAULT_STATUS_CHECK_NAME,
        min_length=1,
        description="Checks API name; teams may pin this in branch protection (§13.10).",
    )
    fail_on: StatusFailOn = Field(
        default="FAIL",
        description="Lowest reviewability level that publishes a `failure` conclusion (§13.10).",
    )
    warn_blocks_merge: bool = Field(
        default=False,
        description="If true, WARN is published as `failure` instead of `neutral` (§13.10).",
    )


class ReviewGateConfig(StrictModel):
    """Effective `.reviewgate.yml` configuration (§12).

    Every field has a default sourced from `docs/DESIGN.md`, so an empty
    YAML document yields a fully-populated, spec-aligned configuration.
    """

    version: ConfigVersion = Field(
        default=CURRENT_CONFIG_VERSION,
        description="Schema version; only 1 is recognized in the MVP (§12).",
    )
    mode: ConfigMode = Field(
        default="app",
        description="Posting authority: hosted app, GitHub Action, or both (§12).",
    )
    llm_reports: bool = Field(
        default=False,
        description="Opt-in for hosted LLM reports; default false per §21.3.",
    )
    thresholds: Thresholds = Field(
        default_factory=Thresholds,
        description="Warn/fail thresholds; per-key defaults follow §10.3.",
    )
    policy: Policy = Field(
        default_factory=Policy,
        description="Reviewability policy toggles (§12).",
    )
    risky_paths: list[str] = Field(
        default_factory=lambda: list(DEFAULT_RISKY_PATHS),
        description="Glob patterns for risky paths; user-provided lists fully replace §10.6 defaults.",
    )
    ignored_paths: list[str] = Field(
        default_factory=lambda: list(DEFAULT_IGNORED_PATHS),
        description="Glob patterns excluded from reviewability heuristics (§12 example).",
    )
    labels: Labels = Field(
        default_factory=Labels,
        description="Label name overrides (§12, §13.9).",
    )
    status_check: StatusCheck = Field(
        default_factory=StatusCheck,
        description="Checks API publication settings (§12, §13.10).",
    )


class ConfigLoadResult(StrictModel):
    """Outcome of :func:`load_config` per §12 malformed-config behavior.

    Attributes:
        config: The effective :class:`ReviewGateConfig` to use for analysis.
            On any malformed input this is the all-defaults instance, so
            callers never need to special-case the warning path.
        warnings: Zero or more :class:`EngineWarning` items describing why
            defaults were used. Empty when YAML parsed and validated cleanly.
    """

    config: ReviewGateConfig = Field(description="Effective configuration to feed the engine.")
    warnings: list[EngineWarning] = Field(
        default_factory=list,
        description="Config-related warnings to merge into the deterministic report.",
    )


def load_config(
    yaml_text: str | None,
    *,
    source_path: str = DEFAULT_CONFIG_PATH,
) -> ConfigLoadResult:
    """Parse `.reviewgate.yml` text and return an effective configuration.

    The function never raises for user-supplied YAML problems. On any parse
    or validation failure it returns the all-defaults configuration plus a
    single :class:`EngineWarning` matching the §12 message template, so the
    surrounding analysis pipeline can keep running.

    Args:
        yaml_text: The raw YAML document, or ``None`` / empty string when
            the repository does not ship a `.reviewgate.yml`.
        source_path: Repo-relative path used only in the warning message.
            Defaults to :data:`DEFAULT_CONFIG_PATH`.

    Returns:
        A :class:`ConfigLoadResult` whose ``warnings`` list is empty on
        success and contains exactly one entry on malformed input.

    Notes:
        I/O lives in the caller. This module never reads from disk or the
        network so it stays inside the §4.1 `reviewgate-core` pure boundary.
    """

    if yaml_text is None or yaml_text.strip() == "":
        return ConfigLoadResult(config=ReviewGateConfig())

    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as err:
        return _defaults_with_warning(source_path, _format_yaml_error(err))

    if raw is None:
        return ConfigLoadResult(config=ReviewGateConfig())

    if not isinstance(raw, dict):
        return _defaults_with_warning(
            source_path,
            f"top-level YAML must be a mapping, got {type(raw).__name__}",
        )

    try:
        cfg = ReviewGateConfig.model_validate(raw)
    except ValidationError as err:
        return _defaults_with_warning(source_path, _format_validation_error(err))

    return ConfigLoadResult(config=cfg)


def _defaults_with_warning(path: str, error: str) -> ConfigLoadResult:
    """Build the all-defaults result plus the §12 config-warning template."""

    message = CONFIG_WARNING_MESSAGE_TEMPLATE.format(path=path, error=error)
    warning = EngineWarning(
        code=CONFIG_WARNING_CODE,
        severity="low",
        message=message,
        evidence={"path": path, "error": error},
    )
    return ConfigLoadResult(config=ReviewGateConfig(), warnings=[warning])


def _format_yaml_error(err: yaml.YAMLError) -> str:
    """Render a PyYAML error into a single-line, human-readable string."""

    text = str(err).strip()
    return text.replace("\n", " ") if text else err.__class__.__name__


def _format_validation_error(err: ValidationError) -> str:
    """Render a Pydantic validation error into a stable, single-line summary."""

    parts: list[str] = []
    for entry in err.errors():
        loc = ".".join(str(part) for part in entry.get("loc", ())) or "<root>"
        parts.append(f"{loc}: {entry.get('msg', 'invalid value')}")
    return "; ".join(parts)


__all__ = [
    "CONFIG_WARNING_CODE",
    "CONFIG_WARNING_MESSAGE_TEMPLATE",
    "CURRENT_CONFIG_VERSION",
    "ConfigLoadResult",
    "ConfigMode",
    "ConfigVersion",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DEPENDENCY_FILES",
    "DEFAULT_GENERATED_PATHS",
    "DEFAULT_IGNORED_PATHS",
    "DEFAULT_LOCKFILES",
    "DEFAULT_MINIFIED_PATHS",
    "DEFAULT_RISKY_PATHS",
    "DEFAULT_SNAPSHOT_PATHS",
    "DEFAULT_STATUS_CHECK_NAME",
    "DEFAULT_TEST_PATHS",
    "DEFAULT_VENDORED_PATHS",
    "FailThresholds",
    "Labels",
    "Policy",
    "ReviewGateConfig",
    "StatusCheck",
    "StatusFailOn",
    "Thresholds",
    "WarnThresholds",
    "load_config",
]
