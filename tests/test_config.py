"""Contract tests for `reviewgate.core.config` (GitHub #3).

Covers docs/DESIGN.md §12 (`.reviewgate.yml` schema and malformed-config
recovery), the §10.3 default thresholds, and the §13.10 status-check
defaults that flow through the configuration model.
"""

from __future__ import annotations

import textwrap
from typing import Final

import pytest

from reviewgate.core.config import (
    CONFIG_WARNING_CODE,
    CONFIG_WARNING_MESSAGE_TEMPLATE,
    DEFAULT_CONFIG_PATH,
    DEFAULT_RISKY_PATHS,
    DEFAULT_STATUS_CHECK_NAME,
    ConfigLoadResult,
    Labels,
    ReviewGateConfig,
    load_config,
)
from reviewgate.core.schemas import EngineWarning

_DESIGN_EXAMPLE_YAML: Final[str] = textwrap.dedent(
    """\
    version: 1

    mode: app

    llm_reports: false

    thresholds:
      warn:
        files_changed: 25
        human_loc_changed: 800
      fail:
        files_changed: 75
        human_loc_changed: 2500

    policy:
      require_linked_issue: true
      require_human_summary: true
      fail_on_risky_paths_without_context: true
      fail_on_huge_pr: true
      warn_blocks_merge: false

    risky_paths:
      - "**/migrations/**"
      - "**/auth/**"
      - "**/billing/**"
      - "**/infra/**"
      - ".github/workflows/**"

    ignored_paths:
      - "**/*.snap"
      - "**/generated/**"

    labels:
      pass: "reviewability-pass"
      warn: "reviewability-warn"
      fail: "reviewability-fail"
      too_large: "too-large"
      missing_context: "missing-context"
      risky_change: "risky-change"
      needs_split: "needs-split"

    status_check:
      enabled: true
      name: "reviewgate/reviewability"
      fail_on: "FAIL"
      warn_blocks_merge: false
    """
)


def _only_warning(result: ConfigLoadResult) -> EngineWarning:
    """Return the single warning a malformed-config result is required to carry."""

    assert len(result.warnings) == 1, result.warnings
    return result.warnings[0]


def test_empty_input_returns_all_defaults() -> None:
    result = load_config(None)
    assert isinstance(result.config, ReviewGateConfig)
    assert result.warnings == []
    assert result.config == ReviewGateConfig()


def test_blank_input_returns_all_defaults() -> None:
    result = load_config("   \n  \n")
    assert result.warnings == []
    assert result.config == ReviewGateConfig()


def test_yaml_null_document_returns_all_defaults() -> None:
    """A YAML document of just `~` parses to None and must behave like absent file."""

    result = load_config("~\n")
    assert result.warnings == []
    assert result.config == ReviewGateConfig()


def test_default_config_matches_design_doc_constants() -> None:
    cfg = ReviewGateConfig()

    assert cfg.version == 1
    assert cfg.mode == "app"
    assert cfg.llm_reports is False

    assert cfg.thresholds.warn.files_changed == 25
    assert cfg.thresholds.warn.human_loc_changed == 800
    assert cfg.thresholds.warn.risky_files_changed == 1
    assert cfg.thresholds.warn.dependency_files_changed == 1
    assert cfg.thresholds.warn.config_files_changed == 1

    assert cfg.thresholds.fail.files_changed == 75
    assert cfg.thresholds.fail.human_loc_changed == 2500
    assert cfg.thresholds.fail.risky_files_without_context == 1

    assert cfg.policy.require_linked_issue is True
    assert cfg.policy.require_human_summary is True
    assert cfg.policy.fail_on_risky_paths_without_context is True
    assert cfg.policy.fail_on_huge_pr is True
    assert cfg.policy.warn_blocks_merge is False

    assert tuple(cfg.risky_paths) == DEFAULT_RISKY_PATHS
    assert cfg.ignored_paths == []

    assert cfg.status_check.enabled is True
    assert cfg.status_check.name == DEFAULT_STATUS_CHECK_NAME
    assert cfg.status_check.fail_on == "FAIL"
    assert cfg.status_check.warn_blocks_merge is False


def test_design_doc_example_yaml_parses_cleanly() -> None:
    result = load_config(_DESIGN_EXAMPLE_YAML)
    assert result.warnings == []

    cfg = result.config
    assert cfg.mode == "app"
    assert cfg.thresholds.fail.human_loc_changed == 2500
    assert cfg.risky_paths == [
        "**/migrations/**",
        "**/auth/**",
        "**/billing/**",
        "**/infra/**",
        ".github/workflows/**",
    ]
    assert cfg.ignored_paths == ["**/*.snap", "**/generated/**"]
    assert cfg.labels.pass_ == "reviewability-pass"
    assert cfg.labels.needs_split == "needs-split"
    assert cfg.status_check.name == "reviewgate/reviewability"


def test_user_risky_paths_replace_defaults_not_extend_them() -> None:
    """Per §12 example, risky_paths is a full override list, not an addition."""

    yaml_text = textwrap.dedent(
        """\
        risky_paths:
          - "infra/prod/**"
        """
    )
    result = load_config(yaml_text)
    assert result.warnings == []
    assert result.config.risky_paths == ["infra/prod/**"]
    assert "**/migrations/**" not in result.config.risky_paths


def test_partial_thresholds_override_only_named_keys() -> None:
    yaml_text = textwrap.dedent(
        """\
        thresholds:
          warn:
            human_loc_changed: 1200
        """
    )
    cfg = load_config(yaml_text).config
    assert cfg.thresholds.warn.human_loc_changed == 1200
    assert cfg.thresholds.warn.files_changed == 25
    assert cfg.thresholds.fail.files_changed == 75


def test_labels_pass_alias_round_trips_via_yaml_key() -> None:
    yaml_text = textwrap.dedent(
        """\
        labels:
          pass: "ok"
          warn: "warn"
        """
    )
    cfg = load_config(yaml_text).config
    assert cfg.labels.pass_ == "ok"
    assert cfg.labels.warn == "warn"


def test_labels_pass_alias_dump_uses_alias_when_requested() -> None:
    labels = Labels()
    dumped_alias = labels.model_dump(by_alias=True)
    assert "pass" in dumped_alias and "pass_" not in dumped_alias


def test_malformed_yaml_returns_defaults_and_warning() -> None:
    yaml_text = "thresholds: {warn: {files_changed: 10\n"
    result = load_config(yaml_text)

    assert result.config == ReviewGateConfig()
    warning = _only_warning(result)
    assert warning.code == CONFIG_WARNING_CODE
    assert warning.severity == "low"
    assert warning.message.startswith("ReviewGate could not parse ")
    assert DEFAULT_CONFIG_PATH in warning.message
    assert warning.evidence["path"] == DEFAULT_CONFIG_PATH
    assert isinstance(warning.evidence["error"], str)


def test_warning_template_constant_matches_design_doc() -> None:
    """Lock the §12 wording so wording drift breaks the test, not production."""

    rendered = CONFIG_WARNING_MESSAGE_TEMPLATE.format(path=".reviewgate.yml", error="x")
    assert rendered == "ReviewGate could not parse .reviewgate.yml: x. Running with defaults."


def test_unknown_top_level_key_falls_back_to_defaults_with_warning() -> None:
    yaml_text = "totally_made_up: 1\n"
    result = load_config(yaml_text)
    warning = _only_warning(result)
    assert "totally_made_up" in warning.evidence["error"]
    assert result.config == ReviewGateConfig()


def test_unknown_nested_key_falls_back_to_defaults_with_warning() -> None:
    yaml_text = textwrap.dedent(
        """\
        thresholds:
          warn:
            unknown_key: 1
        """
    )
    result = load_config(yaml_text)
    warning = _only_warning(result)
    assert "unknown_key" in warning.evidence["error"]


def test_wrong_type_for_threshold_falls_back_to_defaults_with_warning() -> None:
    yaml_text = textwrap.dedent(
        """\
        thresholds:
          warn:
            files_changed: "not-an-int"
        """
    )
    result = load_config(yaml_text)
    warning = _only_warning(result)
    assert "files_changed" in warning.evidence["error"]


def test_negative_threshold_falls_back_to_defaults_with_warning() -> None:
    yaml_text = textwrap.dedent(
        """\
        thresholds:
          warn:
            files_changed: -1
        """
    )
    result = load_config(yaml_text)
    warning = _only_warning(result)
    assert "files_changed" in warning.evidence["error"]


def test_unsupported_version_falls_back_to_defaults_with_warning() -> None:
    yaml_text = "version: 99\n"
    result = load_config(yaml_text)
    warning = _only_warning(result)
    assert "version" in warning.evidence["error"]


def test_invalid_mode_literal_falls_back_to_defaults_with_warning() -> None:
    yaml_text = "mode: yolo\n"
    result = load_config(yaml_text)
    warning = _only_warning(result)
    assert "mode" in warning.evidence["error"]


def test_invalid_status_fail_on_falls_back_to_defaults_with_warning() -> None:
    yaml_text = textwrap.dedent(
        """\
        status_check:
          fail_on: maybe
        """
    )
    result = load_config(yaml_text)
    warning = _only_warning(result)
    assert "fail_on" in warning.evidence["error"]


def test_top_level_yaml_list_falls_back_to_defaults_with_warning() -> None:
    result = load_config("- one\n- two\n")
    warning = _only_warning(result)
    assert "mapping" in warning.evidence["error"]


def test_top_level_scalar_falls_back_to_defaults_with_warning() -> None:
    result = load_config("42\n")
    warning = _only_warning(result)
    assert "mapping" in warning.evidence["error"]


def test_load_config_never_raises_on_arbitrary_user_input() -> None:
    """Defensive sweep: §12 guarantees the loader does not crash analysis."""

    samples: list[str] = [
        "::::",
        "%YAML 9.99\n---\n",
        "version: 1\nrisky_paths: not-a-list\n",
        "thresholds: 5\n",
        "labels: not-a-mapping\n",
    ]
    for sample in samples:
        result = load_config(sample)
        assert isinstance(result, ConfigLoadResult)
        assert result.config == ReviewGateConfig()
        assert len(result.warnings) == 1


def test_source_path_overridden_in_warning_message() -> None:
    yaml_text = "version: 99\n"
    result = load_config(yaml_text, source_path="custom/.reviewgate.yml")
    warning = _only_warning(result)
    assert "custom/.reviewgate.yml" in warning.message
    assert warning.evidence["path"] == "custom/.reviewgate.yml"


def test_status_check_name_must_not_be_empty() -> None:
    yaml_text = textwrap.dedent(
        """\
        status_check:
          name: ""
        """
    )
    result = load_config(yaml_text)
    warning = _only_warning(result)
    assert "name" in warning.evidence["error"]


def test_engine_warning_evidence_is_json_safe() -> None:
    """Smoke-test that the malformed-config warning round-trips through JSON mode."""

    result = load_config("- 1\n")
    warning = _only_warning(result)
    dumped = warning.model_dump_json()
    rebuilt = EngineWarning.model_validate_json(dumped)
    assert rebuilt == warning


@pytest.mark.parametrize(
    "yaml_text, severity",
    [
        pytest.param("thresholds: 5\n", "low", id="wrong-type-thresholds"),
        pytest.param("version: 2\n", "low", id="unsupported-version"),
        pytest.param(": :\n", "low", id="syntax-error"),
    ],
)
def test_all_recovery_warnings_use_low_severity(yaml_text: str, severity: str) -> None:
    """§12 recovery is informational, not blocking; lock severity to `low`."""

    warning = _only_warning(load_config(yaml_text))
    assert warning.severity == severity


def test_config_re_exported_from_reviewgate_core() -> None:
    from reviewgate.core import (
        ConfigLoadResult as ReExportedResult,
        ReviewGateConfig as ReExportedConfig,
        load_config as re_exported_load,
    )

    assert ReExportedResult is ConfigLoadResult
    assert ReExportedConfig is ReviewGateConfig
    assert re_exported_load is load_config
