"""GitHub label ensure/sync for ReviewGate PRs (``docs/DESIGN.md`` §13.9; issue #52).

ReviewGate only mutates labels in the configured :class:`~reviewgate.core.config.Labels`
name set. On each run, labels in that managed set that are absent from the
current suggestion list are removed from the pull request; suggested labels are
added. Arbitrary repository labels applied by humans stay untouched.

Example:
    After analysis, sync labels derived from
    :func:`reviewgate.core.report.suggested_labels`::

        from pydantic import SecretStr

        from reviewgate.app.github.labels import sync_reviewgate_labels_on_issue
        from reviewgate.core.config import Labels, ReviewGateConfig

        cfg = ReviewGateConfig()
        sync_reviewgate_labels_on_issue(
            SecretStr("ghs_…"),
            owner="acme",
            repo="demo",
            issue_number=42,
            desired_labels=["reviewability-pass"],
            labels_config=cfg.labels,
        )
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Final
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from reviewgate.app.github.client import (
    GitHubRestError,
    _installation_auth_headers,
    _raise_for_github_response,
    _validate_repo_segment,
)
from reviewgate.core.config import Labels

logger = logging.getLogger(__name__)

_GITHUB_API_ORIGIN: Final[str] = "https://api.github.com"
_DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0
#: Subtle lavender; GitHub requires six-digit color without ``#``.
_REVIEWGATE_LABEL_COLOR: Final[str] = "D4C5F9"
_LABEL_DESCRIPTION: Final[str] = "Managed by ReviewGate (docs/DESIGN.md §13.9)."


def managed_label_names(labels: Labels) -> frozenset[str]:
    """Return the label names ReviewGate may create, remove, or sync.

    Uses the effective :class:`~reviewgate.core.config.Labels` instance so
    repository-specific renames from ``.reviewgate.yml`` stay consistent.

    Args:
        labels: Label name configuration from the effective ReviewGate config.

    Returns:
        A (typically seven-element) frozenset of distinct non-empty names.
    """

    raw = (
        labels.pass_,
        labels.warn,
        labels.fail,
        labels.too_large,
        labels.missing_context,
        labels.risky_change,
        labels.needs_split,
    )
    return frozenset({n.strip() for n in raw if n.strip()})


def _dedupe_preserve_order(names: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        s = n.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def ensure_reviewgate_labels_exist(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    labels_config: Labels,
    http_client: httpx.Client | None = None,
) -> None:
    """Create missing repository labels for the managed ReviewGate set.

    Calls ``GET /repos/.../labels/{name}`` and ``POST /repos/.../labels`` when
    the name is absent. Idempotent for races (422 treated as success).

    Args:
        installation_token: Installation token with ``issues:write`` (labels
            scope per GitHub App defaults).
        owner: Repository owner login.
        repo: Repository name.
        labels_config: Effective label names to materialize on the repo.
        http_client: Optional shared HTTP client.

    Raises:
        GitHubRestError: On non-recoverable HTTP errors.
    """

    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    base = f"{_GITHUB_API_ORIGIN}/repos/{quote(own, safe='')}/{quote(rep, safe='')}/labels"
    headers = _installation_auth_headers(installation_token)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        for name in sorted(managed_label_names(labels_config)):
            enc = quote(name, safe="")
            get_url = f"{base}/{enc}"
            try:
                response = client.get(get_url, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning(
                    "github_label_get_transport_error",
                    extra={"github_detail": str(exc), "label": name},
                )
                msg = "HTTP transport error while fetching repository label"
                raise GitHubRestError(
                    msg,
                    status_code=None,
                    retriable=True,
                    request_id=None,
                ) from exc
            if response.status_code == httpx.codes.NOT_FOUND:
                try:
                    create_resp = client.post(
                        base,
                        headers=headers,
                        json={
                            "name": name,
                            "color": _REVIEWGATE_LABEL_COLOR,
                            "description": _LABEL_DESCRIPTION,
                        },
                    )
                except httpx.HTTPError as exc:
                    logger.warning(
                        "github_label_create_transport_error",
                        extra={"github_detail": str(exc), "label": name},
                    )
                    msg = "HTTP transport error while creating repository label"
                    raise GitHubRestError(
                        msg,
                        status_code=None,
                        retriable=True,
                        request_id=None,
                    ) from exc
                if create_resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
                    # Race: another writer created the label.
                    logger.info("github_label_create_race_ignored", extra={"label": name})
                    continue
                _raise_for_github_response(
                    operation="create_repository_label",
                    response=create_resp,
                )
                continue
            _raise_for_github_response(operation="get_repository_label", response=response)
    finally:
        if owns_client:
            client.close()


def list_issue_label_names(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    issue_number: int,
    http_client: httpx.Client | None = None,
) -> list[str]:
    """Return current label **names** on a pull request (issue).

    Args:
        installation_token: Installation access token.
        owner: Repository owner login.
        repo: Repository name.
        issue_number: Pull request / issue number.
        http_client: Optional shared HTTP client.

    Returns:
        Label names in GitHub's response order.

    Raises:
        ValueError: When ``issue_number`` is not positive.
        GitHubRestError: On HTTP failure.
    """

    if issue_number < 1:
        msg = "issue_number must be a positive integer"
        raise ValueError(msg)
    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    url = (
        f"{_GITHUB_API_ORIGIN}/repos/"
        f"{quote(own, safe='')}/{quote(rep, safe='')}/issues/{issue_number}/labels"
    )
    headers = _installation_auth_headers(installation_token)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        try:
            response = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "github_list_issue_labels_transport_error",
                extra={"github_detail": str(exc)},
            )
            msg = "HTTP transport error while listing issue labels"
            raise GitHubRestError(
                msg,
                status_code=None,
                retriable=True,
                request_id=None,
            ) from exc
        _raise_for_github_response(operation="list_issue_labels", response=response)
        try:
            parsed: object = response.json()
        except ValueError as exc:
            msg = "GitHub issue labels response was not valid JSON"
            raise GitHubRestError(
                msg,
                status_code=response.status_code,
                retriable=False,
                request_id=response.headers.get("x-github-request-id"),
            ) from exc
        if not isinstance(parsed, list):
            msg = "GitHub issue labels response JSON was not an array"
            raise GitHubRestError(
                msg,
                status_code=response.status_code,
                retriable=False,
                request_id=response.headers.get("x-github-request-id"),
            )
        names: list[str] = []
        for item in parsed:
            if not isinstance(item, dict):
                msg = "GitHub issue labels response contained non-object entries"
                raise GitHubRestError(
                    msg,
                    status_code=response.status_code,
                    retriable=False,
                    request_id=response.headers.get("x-github-request-id"),
                )
            n = item.get("name")
            if isinstance(n, str) and n.strip():
                names.append(n.strip())
        return names
    finally:
        if owns_client:
            client.close()


def _remove_issue_label(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    issue_number: int,
    label_name: str,
    client: httpx.Client,
) -> None:
    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    enc = quote(label_name, safe="")
    url = (
        f"{_GITHUB_API_ORIGIN}/repos/"
        f"{quote(own, safe='')}/{quote(rep, safe='')}/issues/{issue_number}/labels/{enc}"
    )
    headers = _installation_auth_headers(installation_token)
    try:
        response = client.delete(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning(
            "github_remove_label_transport_error",
            extra={"github_detail": str(exc), "label": label_name},
        )
        msg = "HTTP transport error while removing issue label"
        raise GitHubRestError(
            msg,
            status_code=None,
            retriable=True,
            request_id=None,
        ) from exc
    if response.status_code == httpx.codes.NOT_FOUND:
        logger.info(
            "github_remove_label_already_absent",
            extra={"label": label_name, "issue_number": issue_number},
        )
        return
    _raise_for_github_response(operation="remove_issue_label", response=response)


def _add_issue_labels(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    issue_number: int,
    label_names: list[str],
    client: httpx.Client,
) -> None:
    if not label_names:
        return
    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    url = (
        f"{_GITHUB_API_ORIGIN}/repos/"
        f"{quote(own, safe='')}/{quote(rep, safe='')}/issues/{issue_number}/labels"
    )
    headers = _installation_auth_headers(installation_token)
    try:
        response = client.post(url, headers=headers, json={"labels": label_names})
    except httpx.HTTPError as exc:
        logger.warning(
            "github_add_labels_transport_error",
            extra={"github_detail": str(exc)},
        )
        msg = "HTTP transport error while adding issue labels"
        raise GitHubRestError(
            msg,
            status_code=None,
            retriable=True,
            request_id=None,
        ) from exc
    _raise_for_github_response(operation="add_issue_labels", response=response)


def sync_reviewgate_labels_on_issue(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    issue_number: int,
    desired_labels: Sequence[str],
    labels_config: Labels,
    http_client: httpx.Client | None = None,
) -> None:
    """Remove stale managed labels and apply the current suggestion set (§13.9).

    Only labels in :func:`managed_label_names` are ever removed. Labels on the
    issue that are outside that set (for example user-applied ``good first issue``)
    are preserved.

    Args:
        installation_token: Installation access token.
        owner: Repository owner login.
        repo: Repository name.
        issue_number: Pull request number.
        desired_labels: Suggested label names (typically from
            :func:`reviewgate.core.report.suggested_labels`).
        labels_config: Effective :class:`~reviewgate.core.config.Labels` for
            managed-name resolution.
        http_client: Optional shared HTTP client.

    Raises:
        ValueError: When ``desired_labels`` references a name outside the managed
            set, or when ``issue_number`` is invalid.
        GitHubRestError: On HTTP failure.
    """

    if issue_number < 1:
        msg = "issue_number must be a positive integer"
        raise ValueError(msg)
    managed = managed_label_names(labels_config)
    desired = _dedupe_preserve_order(desired_labels)
    unknown = [d for d in desired if d not in managed]
    if unknown:
        msg = f"desired_labels outside managed ReviewGate set: {unknown!r}"
        raise ValueError(msg)

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        current = set(
            list_issue_label_names(
                installation_token,
                owner=owner,
                repo=repo,
                issue_number=issue_number,
                http_client=client,
            ),
        )
        desired_set = set(desired)
        to_remove = sorted(n for n in current if n in managed and n not in desired_set)
        to_add = [n for n in desired if n not in current]

        for name in to_remove:
            _remove_issue_label(
                installation_token,
                owner=owner,
                repo=repo,
                issue_number=issue_number,
                label_name=name,
                client=client,
            )
        _add_issue_labels(
            installation_token,
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            label_names=to_add,
            client=client,
        )
    finally:
        if owns_client:
            client.close()
