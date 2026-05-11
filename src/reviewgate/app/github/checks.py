"""GitHub Checks API mapping for ReviewGate reviewability (``docs/DESIGN.md`` §13.10; issue #53).

Publishes a **completed** check run for a commit with conclusions derived from
deterministic reviewability and ``status_check.warn_blocks_merge`` (WARN maps to
``failure`` when that flag is set; otherwise ``neutral``).

Example:
    Publish after analysis using effective ``.reviewgate.yml``::

        from pydantic import SecretStr

        from reviewgate.app.github.checks import create_completed_reviewability_check_run
        from reviewgate.core.config import ReviewGateConfig

        cfg = ReviewGateConfig()
        create_completed_reviewability_check_run(
            SecretStr("ghs_token"),
            owner="acme",
            repo="demo",
            head_sha="a" * 40,
            reviewability="WARN",
            status_check=cfg.status_check,
        )
"""

from __future__ import annotations

import logging
from typing import Final, Literal
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from reviewgate.app.github.client import (
    GitHubRestError,
    _installation_auth_headers,
    _raise_for_github_response,
    _validate_repo_segment,
)
from reviewgate.core.config import StatusCheck, StatusFailOn
from reviewgate.core.schemas import Reviewability

logger = logging.getLogger(__name__)

_GITHUB_API_ORIGIN: Final[str] = "https://api.github.com"
_DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0
_MIN_HEAD_SHA_LENGTH: Final[int] = 7

_CheckConclusion = Literal["success", "neutral", "failure"]

_REVIEWABILITY_RANK: Final[dict[Reviewability, int]] = {
    "PASS": 0,
    "WARN": 1,
    "FAIL": 2,
}
_FAIL_ON_RANK: Final[dict[StatusFailOn, int]] = {
    "PASS": 0,
    "WARN": 1,
    "FAIL": 2,
}


def reviewability_check_conclusion(
    reviewability: Reviewability,
    *,
    fail_on: StatusFailOn,
    warn_blocks_merge: bool,
) -> _CheckConclusion:
    """Map baseline reviewability to a GitHub check ``conclusion`` (§13.10).

    Args:
        reviewability: Deterministic ``PASS`` / ``WARN`` / ``FAIL``.
        fail_on: Lowest reviewability tier that maps to a ``failure`` conclusion
            when not combined with ``warn_blocks_merge`` (``FAIL`` default).
        warn_blocks_merge: When ``True``, ``WARN`` is published as ``failure`` so
            it blocks merge; when ``False``, ``WARN`` maps to ``neutral`` unless
            ``fail_on`` already treats ``WARN`` as blocking.

    Returns:
        ``success`` for ``PASS``, ``failure`` for ``FAIL``, and ``neutral`` or
        ``failure`` for ``WARN`` depending on ``fail_on`` and ``warn_blocks_merge``.
    """

    r_verdict = _REVIEWABILITY_RANK[reviewability]
    r_fail_on = _FAIL_ON_RANK[fail_on]
    if r_verdict >= r_fail_on:
        return "failure"
    if reviewability == "PASS":
        return "success"
    # WARN with verdict strictly better than fail_on (only possible when fail_on is FAIL)
    return "failure" if warn_blocks_merge else "neutral"


def create_completed_reviewability_check_run(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    reviewability: Reviewability,
    status_check: StatusCheck,
    http_client: httpx.Client | None = None,
) -> int:
    """Create a completed check run for ``head_sha`` (Checks API).

    Args:
        installation_token: Installation token with ``checks:write``.
        owner: Repository owner login.
        repo: Repository name.
        head_sha: Commit SHA the check run attaches to (at least seven chars).
        reviewability: Deterministic verdict to map via
            :func:`reviewability_check_conclusion`.
        status_check: Effective status-check block (name, ``enabled``, and
            ``warn_blocks_merge``).
        http_client: Optional shared HTTP client.

    Returns:
        Numeric ``check_run.id`` from GitHub.

    Raises:
        ValueError: When ``status_check.enabled`` is ``False`` or ``head_sha`` is
            too short after stripping.
        GitHubRestError: On HTTP failure.
    """

    if not status_check.enabled:
        msg = "status_check.enabled is false; skip check run publication"
        raise ValueError(msg)
    ref = head_sha.strip()
    if len(ref) < _MIN_HEAD_SHA_LENGTH:
        msg = "head_sha must be at least 7 characters"
        raise ValueError(msg)

    conclusion = reviewability_check_conclusion(
        reviewability,
        fail_on=status_check.fail_on,
        warn_blocks_merge=status_check.warn_blocks_merge,
    )
    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    url = (
        f"{_GITHUB_API_ORIGIN}/repos/"
        f"{quote(own, safe='')}/{quote(rep, safe='')}/check-runs"
    )
    payload: dict[str, object] = {
        "name": status_check.name,
        "head_sha": ref,
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": "ReviewGate reviewability",
            "summary": (
                f"Deterministic baseline reviewability: **{reviewability}** "
                f"(conclusion `{conclusion}` per §13.10)."
            ),
        },
    }
    headers = _installation_auth_headers(installation_token)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        try:
            response = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.warning(
                "github_check_run_transport_error",
                extra={"github_detail": str(exc)},
            )
            msg = "HTTP transport error while creating check run"
            raise GitHubRestError(
                msg,
                status_code=None,
                retriable=True,
                request_id=None,
            ) from exc
        _raise_for_github_response(operation="create_check_run", response=response)
        try:
            body: object = response.json()
        except ValueError as exc:
            msg = "GitHub check run response was not valid JSON"
            raise GitHubRestError(
                msg,
                status_code=response.status_code,
                retriable=False,
                request_id=response.headers.get("x-github-request-id"),
            ) from exc
        if not isinstance(body, dict):
            msg = "GitHub check run response JSON was not an object"
            raise GitHubRestError(
                msg,
                status_code=response.status_code,
                retriable=False,
                request_id=response.headers.get("x-github-request-id"),
            )
        cid = body.get("id")
        if not isinstance(cid, int):
            msg = "GitHub check run response missing integer id"
            raise GitHubRestError(
                msg,
                status_code=response.status_code,
                retriable=False,
                request_id=response.headers.get("x-github-request-id"),
            )
        return cid
    finally:
        if owns_client:
            client.close()
