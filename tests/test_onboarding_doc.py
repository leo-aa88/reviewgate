"""Contract tests for `docs/ONBOARDING.md` (issue #27).

The onboarding doc is a beta-team artifact: it has to mention every
acceptance criterion from #27 (App install + repo selection,
`.reviewgate.yml`, optional Action, required-check setup per §8 and
§5.1) and explicitly call out the §21.3 default that LLM reports are
opt-in. Locking these strings in tests means a future doc rewrite
that drops one of the criteria fails CI before it can ship.

Pure: only reads files inside the repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_ONBOARDING: Final[Path] = _REPO_ROOT / "docs" / "ONBOARDING.md"
_TOP_README: Final[Path] = _REPO_ROOT / "README.md"


@pytest.fixture(scope="module")
def onboarding_text() -> str:
    return _ONBOARDING.read_text(encoding="utf-8")


def test_onboarding_doc_exists_at_documented_path() -> None:
    """The README links here; the file has to live at this path."""

    assert _ONBOARDING.is_file(), (
        f"docs/ONBOARDING.md is missing; the README links to "
        f"{_ONBOARDING.relative_to(_REPO_ROOT)}"
    )


@pytest.mark.parametrize(
    "phrase",
    [
        # GitHub App install + repo selection (§8.1, #27 acceptance).
        "Install ReviewGate",
        "Only select repositories",
        # `.reviewgate.yml` configuration (§12, #27 acceptance).
        ".reviewgate.yml",
        "version: 1",
        # Optional GitHub Action (§14, #27 acceptance).
        "leo-aa88/reviewgate/src/reviewgate_action@v1",
        "fail-on: FAIL",
        # Required status check (§8.4, #27 acceptance).
        "required status check",
        "Branch protection",
        # §14.1 coexistence rules.
        "mode: app",
        "mode: action",
        "mode: auto",
        "mode: quiet",
        # §21.3 LLM opt-in (#27 explicit acceptance).
        "llm_reports: false",
        "opt-in",
        "Deterministic-only mode",
    ],
)
def test_onboarding_doc_covers_acceptance_criteria(
    onboarding_text: str, phrase: str
) -> None:
    """Each phrase pins a #27 acceptance criterion or §-cross-reference.

    The list mirrors the issue body: GitHub App install, repo
    selection, adding `.reviewgate.yml`, optional Action with the
    documented `uses:` form and `fail-on` default, required status
    check setup, every §14.1 coexistence value, the §21.3 LLM
    opt-in line, and the deterministic-only fallback.
    """

    assert phrase in onboarding_text, (
        f"docs/ONBOARDING.md must mention {phrase!r} so the #27 "
        "acceptance criteria stay covered (see the issue body)"
    )


def test_onboarding_doc_links_design_doc_sections(onboarding_text: str) -> None:
    """The doc must cite the §-numbered sections #27 calls out.

    `docs/DESIGN.md` is the source of truth; the onboarding guide
    has to reference §8.1 (App install), §12 (config), §14.1
    (coexistence), and §21.3 (LLM opt-in) so a reader can dig
    deeper without guessing where to look.
    """

    for section in ("§8.1", "§12", "§14.1", "§21.3"):
        assert section in onboarding_text, (
            f"docs/ONBOARDING.md must cite design-doc {section}"
        )


def test_top_level_readme_links_onboarding_doc() -> None:
    """The README must link to the onboarding doc.

    Discoverability guard: a beta team starts at the README. If the
    onboarding doc is not linked from there, it might as well not
    exist.
    """

    readme = _TOP_README.read_text(encoding="utf-8")
    assert "docs/ONBOARDING.md" in readme, (
        "Top-level README must link to docs/ONBOARDING.md so beta "
        "teams can find the install walkthrough"
    )


def test_onboarding_doc_recommends_starting_small() -> None:
    """The doc must steer new installs at one or two repos first.

    A "install organisation-wide on day one" recommendation is the
    fastest way to get a beta cancelled when a default verdict
    surprises a team. The doc has to tell operators to start small.
    """

    text = _ONBOARDING.read_text(encoding="utf-8").lower()
    assert "one or two repos" in text or "start with one" in text, (
        "docs/ONBOARDING.md must explicitly recommend starting with a "
        "small set of repos before rolling org-wide"
    )
