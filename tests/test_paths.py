"""Tests for :mod:`reviewgate.core.paths` against \u00a710.6\u2013\u00a710.9 default lists.

Locks the gitignore-style semantics required by the categorizer (#9):

* Path segments after ``**/`` (including the empty case) match.
* Bare basenames such as ``Dockerfile`` match anywhere in the tree.
* Rooted patterns such as ``vendor/**`` only match at the repo root.
* Non-matches stay non-matches (no false positives across categories).

A regression on any of these would silently break human-LOC accounting
(\u00a710.4) and risky-path warnings (\u00a710.10), so each category is asserted
with an explicit positive *and* negative table.
"""

from __future__ import annotations

import pytest

from reviewgate.core.config import (
    DEFAULT_DEPENDENCY_FILES,
    DEFAULT_GENERATED_PATHS,
    DEFAULT_LOCKFILES,
    DEFAULT_MINIFIED_PATHS,
    DEFAULT_RISKY_PATHS,
    DEFAULT_SNAPSHOT_PATHS,
    DEFAULT_TEST_PATHS,
    DEFAULT_VENDORED_PATHS,
)
from reviewgate.core.paths import PathMatcher, match_any


# --- risky-path patterns (\u00a710.6) ------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param("src/migrations/0001.sql", id="nested-migrations"),
        pytest.param("migrations/0001.sql", id="root-migrations"),
        pytest.param("backend/migration/0002.sql", id="singular-migration"),
        pytest.param("services/auth/login.py", id="auth-segment"),
        pytest.param("services/authentication/sso.py", id="authentication-segment"),
        pytest.param("billing/invoice.py", id="billing-segment"),
        pytest.param("payments/stripe.py", id="payments-segment"),
        pytest.param("infra/k8s/deployment.yaml", id="infra-segment"),
        pytest.param("terraform/main.tf", id="terraform-segment"),
        pytest.param(".github/workflows/ci.yml", id="actions-workflow"),
        pytest.param("Dockerfile", id="root-dockerfile"),
        pytest.param("services/api/Dockerfile", id="nested-dockerfile"),
        pytest.param("docker-compose.yml", id="root-compose"),
        pytest.param("compose.yml", id="root-compose-modern"),
    ],
)
def test_risky_paths_match_known_examples(filename: str) -> None:
    """Every \u00a710.6 example surface should hit `DEFAULT_RISKY_PATHS`."""

    assert match_any(filename, DEFAULT_RISKY_PATHS), filename


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param("src/utils/helpers.py", id="plain-source"),
        pytest.param("docs/index.md", id="docs-file"),
        pytest.param("README.md", id="readme"),
        pytest.param("authservice.py", id="basename-fragment-not-segment"),
        pytest.param("backend/payments_history.py", id="payments-fragment-not-segment"),
        pytest.param("Dockerfile.template", id="dockerfile-suffix-not-exact"),
    ],
)
def test_risky_paths_do_not_match_unrelated_files(filename: str) -> None:
    """Names that merely *contain* a risky token must not match."""

    assert not match_any(filename, DEFAULT_RISKY_PATHS), filename


# --- dependency / lockfile patterns (\u00a710.7) ------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
    ],
)
def test_dependency_files_match_root_manifests(filename: str) -> None:
    """\u00a710.7 manifests matched at the repo root (gitwildmatch basename rule)."""

    assert match_any(filename, DEFAULT_DEPENDENCY_FILES), filename


def test_dependency_files_match_nested_manifests() -> None:
    """Monorepos: a nested `package.json` is still a dependency manifest."""

    assert match_any("frontend/package.json", DEFAULT_DEPENDENCY_FILES)
    assert match_any("services/api/pyproject.toml", DEFAULT_DEPENDENCY_FILES)


@pytest.mark.parametrize(
    "filename",
    [
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "uv.lock",
        "go.sum",
        "Cargo.lock",
    ],
)
def test_lockfiles_match_known_lockfiles(filename: str) -> None:
    """Each \u00a710.7 lockfile surface matches as expected."""

    assert match_any(filename, DEFAULT_LOCKFILES), filename


def test_lockfiles_do_not_match_arbitrary_yaml() -> None:
    """A random `.yaml` file must not be misclassified as a lockfile."""

    assert not match_any("config/settings.yaml", DEFAULT_LOCKFILES)
    assert not match_any("docs/spec.json", DEFAULT_LOCKFILES)


# --- generated / vendored / minified / snapshot (\u00a710.8) ------------------


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param("src/generated/api.go", id="generated-segment"),
        pytest.param("services/gen/types.ts", id="gen-segment"),
        pytest.param("api/types.pb.go", id="protobuf-go"),
        pytest.param("clients/openapi.generated.ts", id="openapi-generated-ts"),
        pytest.param("clients/openapi.generated.py", id="openapi-generated-py"),
        pytest.param("schema.generated.json", id="schema-generated-json"),
    ],
)
def test_generated_paths_match_codegen_outputs(filename: str) -> None:
    """\u00a710.8 codegen surfaces should match `DEFAULT_GENERATED_PATHS`."""

    assert match_any(filename, DEFAULT_GENERATED_PATHS), filename


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param("vendor/lib/foo.go", id="vendor-go"),
        pytest.param("third_party/lib.cpp", id="third_party"),
        pytest.param("node_modules/react/index.js", id="node_modules"),
    ],
)
def test_vendored_paths_match_vendor_dirs(filename: str) -> None:
    """\u00a710.8 vendor-tree paths match (rooted patterns)."""

    assert match_any(filename, DEFAULT_VENDORED_PATHS), filename


def test_vendored_paths_are_root_anchored() -> None:
    """`vendor/**` must not match a `vendor` segment buried mid-tree.

    `pathspec` gitwildmatch treats `foo/**` as anchored to the repo root.
    A nested `services/vendor/...` is treated as project source, not a
    vendored dependency.
    """

    assert not match_any("services/vendor/lib/foo.go", DEFAULT_VENDORED_PATHS)


@pytest.mark.parametrize(
    "filename",
    [
        "static/app.min.js",
        "dist/main.min.css",
        "public/js/site.min.js",
    ],
)
def test_minified_paths_match_min_assets(filename: str) -> None:
    """\u00a710.8 minified assets match by basename suffix."""

    assert match_any(filename, DEFAULT_MINIFIED_PATHS), filename


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param("__snapshots__/Component.snap", id="snapshots-dir"),
        pytest.param("ui/__snapshots__/Button.test.ts.snap", id="nested-snapshots-dir"),
        pytest.param("fixtures/payload.snap", id="loose-snap"),
    ],
)
def test_snapshot_paths_match_snap_files(filename: str) -> None:
    """\u00a710.8 snapshot patterns cover both directory and bare-suffix forms."""

    assert match_any(filename, DEFAULT_SNAPSHOT_PATHS), filename


# --- test-path patterns (\u00a710.9) -----------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param("services/api/test/conftest.py", id="test-dir"),
        pytest.param("services/api/tests/test_routes.py", id="tests-dir"),
        pytest.param("frontend/src/__tests__/Button.spec.tsx", id="js-tests-dir"),
        pytest.param("foo.test.ts", id="basename-test"),
        pytest.param("bar.spec.js", id="basename-spec"),
        pytest.param("test_module.py", id="python-test-prefix"),
        pytest.param("module_test.go", id="go-test-suffix"),
    ],
)
def test_test_paths_match_test_surfaces(filename: str) -> None:
    """Every \u00a710.9 test convention is recognized."""

    assert match_any(filename, DEFAULT_TEST_PATHS), filename


def test_test_paths_do_not_match_production_modules() -> None:
    """Production modules with similar names must not be misclassified."""

    assert not match_any("services/api/routes.py", DEFAULT_TEST_PATHS)
    assert not match_any("services/api/test_helpers_lib.go", DEFAULT_TEST_PATHS), (
        "`test_helpers_lib.go` should not match `*_test.go`; only the suffix"
    )


# --- categorical separation -------------------------------------------------


def test_categories_are_disjoint_for_unrelated_paths() -> None:
    """A plain source file should not match any \u00a710.6\u2013\u00a710.9 category."""

    plain = "src/reviewgate/core/aggregate.py"
    for patterns in (
        DEFAULT_RISKY_PATHS,
        DEFAULT_DEPENDENCY_FILES,
        DEFAULT_LOCKFILES,
        DEFAULT_GENERATED_PATHS,
        DEFAULT_VENDORED_PATHS,
        DEFAULT_MINIFIED_PATHS,
        DEFAULT_SNAPSHOT_PATHS,
        DEFAULT_TEST_PATHS,
    ):
        assert not match_any(plain, patterns), patterns


# --- PathMatcher object semantics -------------------------------------------


def test_path_matcher_caches_compiled_patterns() -> None:
    """Repeated calls reuse the compiled spec; pattern view is stable."""

    matcher = PathMatcher(DEFAULT_RISKY_PATHS)
    assert matcher.patterns == DEFAULT_RISKY_PATHS
    assert matcher.matches("Dockerfile")
    assert matcher.matches("Dockerfile")  # second call must keep working
    assert not matcher.matches("README.md")


def test_path_matcher_filter_preserves_order() -> None:
    """`filter` returns matching files in the input order."""

    matcher = PathMatcher(DEFAULT_RISKY_PATHS)
    files = [
        "README.md",
        "Dockerfile",
        "src/utils.py",
        ".github/workflows/ci.yml",
        "docs/index.md",
    ]
    assert matcher.filter(files) == ["Dockerfile", ".github/workflows/ci.yml"]


def test_path_matcher_with_empty_patterns_matches_nothing() -> None:
    """An empty pattern list yields a matcher that always returns False.

    Used when a category is disabled by config; constructing the matcher
    must remain safe and return ``False`` for every input.
    """

    matcher = PathMatcher(())
    assert matcher.patterns == ()
    assert not matcher.matches("anything")
    assert matcher.filter(["a", "b", "c"]) == []


def test_path_matcher_accepts_generator_patterns() -> None:
    """Pattern lists may be generators; the matcher materializes them once.

    Guard against a refactor that would silently consume a generator and
    leave :attr:`patterns` empty after the first call.
    """

    def gen() -> object:
        for p in ("Dockerfile", "*.py"):
            yield p

    # Iterable[str]-typed argument; the generator is materialized inside.
    matcher = PathMatcher(p for p in ("Dockerfile", "*.py"))
    assert matcher.patterns == ("Dockerfile", "*.py")
    assert matcher.matches("Dockerfile")
    assert matcher.matches("module.py")
