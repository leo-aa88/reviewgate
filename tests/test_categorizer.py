"""Tests for :mod:`reviewgate.core.categorizer` against \u00a710.5 / \u00a710.6 / \u00a710.4.

Locks the deterministic file-classification contract that downstream
heuristics (#10 size, #13 risky, #14 mixed concern) rely on:

* Every \u00a710.5 category name (16 total) is reachable on at least one file.
* Files can carry multiple categories (\u00a710.5 example
  ``app/auth/session.ts`` -> ``["source", "auth"]``).
* ``risky`` is computed from the user-configurable \u00a710.6 list.
* ``human_authored`` is ``False`` for ``lockfile`` / ``generated`` /
  ``snapshot`` / ``vendored`` / ``minified`` per \u00a710.4.

Tests parameterise every category boundary with a descriptive ``id``
so a regression names exactly which rule broke.
"""

from __future__ import annotations

from typing import Final

import pytest

from reviewgate.core.categorizer import (
    Categorizer,
    categorize_changed_files,
)
from reviewgate.core.schemas import (
    ChangedFile,
    FileCategory,
    FileCategoryRow,
    FileStatus,
)

_DEFAULT_STATUS: Final[FileStatus] = "modified"


def _file(filename: str, *, changes: int = 10) -> ChangedFile:
    """Build a minimal :class:`ChangedFile` with a realistic change count.

    Only ``filename`` and ``changes`` matter for categorization; the rest
    are pinned to spec-valid placeholders so the helper stays focused.
    """

    return ChangedFile(
        filename=filename,
        status=_DEFAULT_STATUS,
        additions=changes,
        deletions=0,
        changes=changes,
    )


def _categorize_one(filename: str) -> FileCategoryRow:
    """Categorize a single file with the default \u00a710.6 risky-path list."""

    return Categorizer().categorize(_file(filename))


# --- single-category baselines (every \u00a710.5 label is reachable) -----------


@pytest.mark.parametrize(
    ("filename", "expected_category"),
    [
        pytest.param("src/utils.py", "source", id="source-py"),
        pytest.param("services/api/handler.go", "source", id="source-go"),
        pytest.param("frontend/src/Button.tsx", "source", id="source-tsx"),
        pytest.param("docs/index.md", "docs", id="docs-md"),
        pytest.param("README.md", "docs", id="docs-readme"),
        pytest.param("CHANGELOG", "docs", id="docs-changelog-no-ext"),
        pytest.param("LICENSE", "docs", id="docs-license"),
        pytest.param("config/settings.yaml", "config", id="config-yaml"),
        pytest.param(".editorconfig", "config", id="config-editorconfig"),
        pytest.param("Makefile", "config", id="config-makefile"),
        pytest.param("logo.png", "asset", id="asset-png"),
        pytest.param("fonts/OpenSans.woff2", "asset", id="asset-font"),
    ],
)
def test_extension_only_files_get_expected_category(
    filename: str,
    expected_category: FileCategory,
) -> None:
    row = _categorize_one(filename)
    assert expected_category in row.categories, row.categories


@pytest.mark.parametrize(
    ("filename", "expected_category"),
    [
        pytest.param("package.json", "dependency", id="dep-package-json"),
        pytest.param("frontend/package.json", "dependency", id="dep-package-json-nested"),
        pytest.param("pyproject.toml", "dependency", id="dep-pyproject"),
        pytest.param("Cargo.toml", "dependency", id="dep-cargo-toml"),
        pytest.param("yarn.lock", "lockfile", id="lock-yarn"),
        pytest.param("package-lock.json", "lockfile", id="lock-npm"),
        pytest.param("uv.lock", "lockfile", id="lock-uv"),
        pytest.param("go.sum", "lockfile", id="lock-go-sum"),
        pytest.param("src/migrations/001.sql", "migration", id="migration-sql"),
        pytest.param("backend/migration/up.go", "migration", id="migration-go"),
        pytest.param("infra/k8s/deployment.yaml", "infra", id="infra-k8s"),
        pytest.param("terraform/main.tf", "infra", id="infra-tf"),
        pytest.param("Dockerfile", "infra", id="infra-dockerfile"),
        pytest.param(".github/workflows/ci.yml", "infra", id="infra-actions"),
        pytest.param("services/auth/login.py", "auth", id="auth-py"),
        pytest.param("authentication/sso.go", "auth", id="auth-go"),
        pytest.param("billing/invoice.py", "billing", id="billing-py"),
        pytest.param("payments/stripe.go", "billing", id="billing-payments"),
        pytest.param("src/generated/api.go", "generated", id="generated-dir"),
        pytest.param("schema.generated.json", "generated", id="generated-suffix"),
        pytest.param("ui/__snapshots__/Btn.snap", "snapshot", id="snapshot-dir"),
        pytest.param("fixtures/payload.snap", "snapshot", id="snapshot-suffix"),
        pytest.param("vendor/lib/foo.go", "vendored", id="vendored-go"),
        pytest.param("node_modules/react/index.js", "vendored", id="vendored-node-modules"),
        pytest.param("static/site.min.js", "minified", id="minified-js"),
        pytest.param("dist/main.min.css", "minified", id="minified-css"),
        pytest.param("tests/test_x.py", "test", id="test-pytest"),
        pytest.param("services/api/__tests__/handler.spec.ts", "test", id="test-jest"),
    ],
)
def test_pattern_files_get_expected_category(
    filename: str,
    expected_category: FileCategory,
) -> None:
    row = _categorize_one(filename)
    assert expected_category in row.categories, row.categories


def test_unknown_files_fall_back_to_unknown_category() -> None:
    """Files that match no rule are labelled ``unknown`` (\u00a710.5 last item)."""

    row = _categorize_one("data/payload.bin")
    assert row.categories == ["unknown"]


# --- multi-label rules (the \u00a710.5 hallmark) ------------------------------


def test_design_doc_example_yields_source_and_auth() -> None:
    """\u00a710.5 example: ``app/auth/session.ts`` -> ``["source", "auth"]``."""

    row = _categorize_one("app/auth/session.ts")
    assert "source" in row.categories
    assert "auth" in row.categories
    assert row.risky is True
    assert row.human_authored is True


def test_test_files_keep_source_label_alongside_test() -> None:
    """A test file written in Python is both ``test`` and ``source``."""

    row = _categorize_one("tests/test_categorizer.py")
    assert "test" in row.categories
    assert "source" in row.categories


def test_minified_js_is_minified_and_source_but_not_human_authored() -> None:
    """``static/app.min.js`` is source-by-extension but excluded by \u00a710.4."""

    row = _categorize_one("static/app.min.js")
    assert "minified" in row.categories
    assert "source" in row.categories
    assert row.human_authored is False


def test_dependency_manifest_is_not_double_labeled_as_config() -> None:
    """``pyproject.toml`` is a dependency manifest, not generic config.

    Both rules technically match (`.toml` extension + dependency
    pattern). The categorizer prefers the more specific ``dependency``
    label and suppresses ``config`` to avoid noisy double-counting.
    """

    row = _categorize_one("pyproject.toml")
    assert "dependency" in row.categories
    assert "config" not in row.categories


def test_lockfile_is_not_double_labeled_as_config() -> None:
    """Same suppression rule for lockfiles (``poetry.lock``, ``yarn.lock``)."""

    row = _categorize_one("poetry.lock")
    assert "lockfile" in row.categories
    assert "config" not in row.categories


def test_migration_sql_is_migration_and_source() -> None:
    """SQL migration is both ``migration`` (risky) and ``source``."""

    row = _categorize_one("backend/migrations/0001_create_users.sql")
    assert "migration" in row.categories
    assert "source" in row.categories
    assert row.risky is True


# --- ``risky`` derivation ---------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "Dockerfile",
        "infra/k8s/deployment.yaml",
        "src/auth/sso.py",
        "billing/invoice.py",
        "src/migrations/0001.sql",
    ],
)
def test_default_risky_paths_set_risky_true(filename: str) -> None:
    """Every \u00a710.6 default category surface flips ``risky`` on."""

    row = _categorize_one(filename)
    assert row.risky is True


@pytest.mark.parametrize(
    "filename",
    [
        "src/utils/helpers.py",
        "docs/index.md",
        "tests/test_helpers.py",
        "package.json",
    ],
)
def test_non_risky_files_have_risky_false(filename: str) -> None:
    row = _categorize_one(filename)
    assert row.risky is False


def test_custom_risky_patterns_override_defaults() -> None:
    """User-supplied ``risky_patterns`` replaces \u00a710.6 for the boolean only.

    Spec-defined category labels (``auth``, ``billing``, ``infra``,
    ``migration``) keep using their hardcoded subsets so a user cannot
    accidentally erase those category names by editing ``risky_paths``.
    """

    custom = ("**/secret/**",)
    cat = Categorizer(risky_patterns=custom)

    secret = cat.categorize(_file("config/secret/keys.yaml"))
    assert secret.risky is True

    auth = cat.categorize(_file("services/auth/login.py"))
    assert auth.risky is False, "custom list excludes auth, so risky=False"
    assert "auth" in auth.categories, "auth label still emitted from \u00a710.6 subset"


# --- ``human_authored`` per \u00a710.4 ---------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param("yarn.lock", id="lockfile"),
        pytest.param("src/generated/api.go", id="generated"),
        pytest.param("ui/__snapshots__/Btn.snap", id="snapshot"),
        pytest.param("vendor/lib/foo.go", id="vendored"),
        pytest.param("static/app.min.js", id="minified"),
    ],
)
def test_excluded_categories_flip_human_authored_false(filename: str) -> None:
    """\u00a710.4: lockfile/generated/snapshot/vendored/minified -> not human."""

    row = _categorize_one(filename)
    assert row.human_authored is False


@pytest.mark.parametrize(
    "filename",
    [
        "src/utils.py",
        "tests/test_x.py",
        "docs/index.md",
        "config/settings.yaml",
        "src/auth/login.py",
        "infra/k8s/deployment.yaml",
        "package.json",
    ],
)
def test_other_categories_keep_human_authored_true(filename: str) -> None:
    """Files outside the \u00a710.4 exclusion stay counted toward human LOC."""

    row = _categorize_one(filename)
    assert row.human_authored is True


# --- output shape & invariants ----------------------------------------------


def test_categorize_preserves_changes_field() -> None:
    """``changes`` from the input passes through unchanged."""

    row = Categorizer().categorize(_file("src/utils.py", changes=42))
    assert row.changes == 42


def test_every_category_label_in_design_doc_is_reachable() -> None:
    """Sanity: at least one filename produces every \u00a710.5 label name.

    Guards against a future refactor that drops a category from the
    rule table; without this test the change would still pass the
    per-category tests because they each only assert *one* label.
    """

    samples = [
        "src/utils.py",  # source
        "tests/test_x.py",  # test, source
        "docs/index.md",  # docs
        "config/settings.yaml",  # config
        "package.json",  # dependency
        "yarn.lock",  # lockfile
        "src/migrations/001.sql",  # migration, source
        "infra/k8s/deployment.yaml",  # infra
        "src/auth/login.py",  # auth, source
        "billing/invoice.py",  # billing, source
        "src/generated/api.go",  # generated, source
        "ui/__snapshots__/Btn.snap",  # snapshot
        "vendor/lib/foo.go",  # vendored, source
        "static/app.min.js",  # minified, source
        "logo.png",  # asset
        "data/payload.bin",  # unknown
    ]

    seen: set[FileCategory] = set()
    for name in samples:
        seen.update(_categorize_one(name).categories)

    expected: set[FileCategory] = {
        "source",
        "test",
        "docs",
        "config",
        "dependency",
        "lockfile",
        "migration",
        "infra",
        "auth",
        "billing",
        "generated",
        "snapshot",
        "vendored",
        "minified",
        "asset",
        "unknown",
    }
    missing = expected - seen
    assert not missing, f"\u00a710.5 categories never reached: {sorted(missing)}"


def test_categorize_changed_files_preserves_input_order() -> None:
    """Convenience helper returns rows in the input file order."""

    files = [_file(n) for n in ("z.py", "a.py", "m.py")]
    rows = categorize_changed_files(files)
    assert [r.filename for r in rows] == ["z.py", "a.py", "m.py"]


def test_categorizer_instance_is_reusable_across_files() -> None:
    """A single :class:`Categorizer` can categorize many files (matcher cache)."""

    cat = Categorizer()
    a = cat.categorize(_file("src/utils.py"))
    b = cat.categorize(_file("Dockerfile"))
    c = cat.categorize(_file("yarn.lock"))
    assert "source" in a.categories and a.risky is False
    assert "infra" in b.categories and b.risky is True
    assert "lockfile" in c.categories and c.human_authored is False


def test_dotfiles_without_extension_are_not_misclassified_as_source() -> None:
    """``.gitignore`` and friends should be ``config``, not ``unknown`` or ``source``.

    Regression guard for the leading-dot edge case in :func:`_extension`.
    """

    row = _categorize_one(".gitignore")
    assert "config" in row.categories
    assert "source" not in row.categories
