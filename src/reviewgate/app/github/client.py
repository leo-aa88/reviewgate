"""GitHub REST read helpers (``docs/DESIGN.md`` §13.5; issue #40).

Callers obtain an installation access token via
:func:`reviewgate.app.github.auth.fetch_installation_access_token`, then pass
``installation_token`` into the functions in this module. For **installation**
REST calls, GitHub documents sending the token in the ``Authorization`` header
as ``Bearer <installation_access_token>`` (the same ``Bearer`` prefix used for
personal access tokens and OAuth user tokens; it is distinct from the
short-lived **JWT** used only when calling ``/app/*`` installation-token
exchange endpoints in :mod:`reviewgate.app.github.auth`).

Responses are parsed as JSON; HTTP failures raise :class:`GitHubRestError` with
a ``retriable`` flag for backoff logic.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Final
from urllib.parse import quote

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)

_GITHUB_API_ORIGIN: Final[str] = "https://api.github.com"
_DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0
_GITHUB_ACCEPT_HEADER: Final[str] = "application/vnd.github+json"
_GITHUB_API_VERSION: Final[str] = "2022-11-28"
_FILES_PER_PAGE: Final[int] = 100
_MAX_FILE_PAGES: Final[int] = 500


class GitHubRestError(RuntimeError):
    """Raised when a GitHub REST call fails after a response (or transport) error.

    Attributes:
        status_code: HTTP status when available; ``None`` for transport errors.
        retriable: Whether callers should retry with backoff.
        request_id: ``X-GitHub-Request-Id`` header value when GitHub sent one.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        retriable: bool,
        request_id: str | None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retriable = retriable
        self.request_id = request_id


def _validate_repo_segment(label: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned or "/" in cleaned or ".." in cleaned:
        msg = f"invalid GitHub {label} slug"
        raise ValueError(msg)
    return cleaned


def _installation_auth_headers(installation_token: SecretStr) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {installation_token.get_secret_value()}",
        "Accept": _GITHUB_ACCEPT_HEADER,
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }


def _classify_retriable_http(status_code: int, response: httpx.Response) -> bool:
    if status_code in (408, 429, 500, 502, 503):
        return True
    if status_code == 403:
        remaining = response.headers.get("x-ratelimit-remaining")
        try:
            if remaining is not None and int(remaining) == 0:
                return True
        except ValueError:
            return False
    return False


def _log_github_failure(
    *,
    operation: str,
    response: httpx.Response | None,
    retriable: bool,
    detail: str,
) -> None:
    extra: dict[str, object] = {
        "github_operation": operation,
        "github_retriable": retriable,
        "github_detail": detail,
    }
    if response is not None:
        extra["github_status_code"] = response.status_code
        extra["github_request_id"] = response.headers.get("x-github-request-id")
        extra["github_url"] = str(response.request.url)
    logger.warning("github_rest_failure", extra=extra)


def _raise_for_github_response(
    *,
    operation: str,
    response: httpx.Response,
) -> None:
    if response.is_success:
        return
    retriable = _classify_retriable_http(response.status_code, response)
    _log_github_failure(
        operation=operation,
        response=response,
        retriable=retriable,
        detail=response.text[:500],
    )
    msg = f"GitHub REST {operation} failed (HTTP {response.status_code})"
    raise GitHubRestError(
        msg,
        status_code=response.status_code,
        retriable=retriable,
        request_id=response.headers.get("x-github-request-id"),
    )


def fetch_pull_request(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    pull_number: int,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Return the JSON document for ``GET /repos/{{owner}}/{{repo}}/pulls/{{n}}``."""

    if pull_number < 1:
        msg = "pull_number must be a positive integer"
        raise ValueError(msg)
    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    url = (
        f"{_GITHUB_API_ORIGIN}/repos/"
        f"{quote(own, safe='')}/{quote(rep, safe='')}/pulls/{pull_number}"
    )
    headers = _installation_auth_headers(installation_token)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        try:
            response = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            _log_github_failure(
                operation="fetch_pull_request",
                response=None,
                retriable=True,
                detail=str(exc),
            )
            msg = "HTTP transport error while fetching pull request"
            raise GitHubRestError(
                msg,
                status_code=None,
                retriable=True,
                request_id=None,
            ) from exc
        _raise_for_github_response(operation="fetch_pull_request", response=response)
    finally:
        if owns_client:
            client.close()

    try:
        body: object = response.json()
    except ValueError as exc:
        msg = "GitHub pull response was not valid JSON"
        raise GitHubRestError(
            msg,
            status_code=response.status_code,
            retriable=False,
            request_id=response.headers.get("x-github-request-id"),
        ) from exc
    if not isinstance(body, dict):
        msg = "GitHub pull response JSON was not an object"
        raise GitHubRestError(
            msg,
            status_code=response.status_code,
            retriable=False,
            request_id=response.headers.get("x-github-request-id"),
        )
    return body


def fetch_pull_request_files(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    pull_number: int,
    http_client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Return all file objects, following ``per_page=100`` pagination."""

    if pull_number < 1:
        msg = "pull_number must be a positive integer"
        raise ValueError(msg)
    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    base_url = (
        f"{_GITHUB_API_ORIGIN}/repos/"
        f"{quote(own, safe='')}/{quote(rep, safe='')}/pulls/{pull_number}/files"
    )
    headers = _installation_auth_headers(installation_token)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    aggregated: list[dict[str, Any]] = []
    page = 1
    try:
        while True:
            try:
                response = client.get(
                    base_url,
                    headers=headers,
                    params={"per_page": _FILES_PER_PAGE, "page": page},
                )
            except httpx.HTTPError as exc:
                _log_github_failure(
                    operation="fetch_pull_request_files",
                    response=None,
                    retriable=True,
                    detail=str(exc),
                )
                msg = "HTTP transport error while fetching pull request files"
                raise GitHubRestError(
                    msg,
                    status_code=None,
                    retriable=True,
                    request_id=None,
                ) from exc
            _raise_for_github_response(
                operation="fetch_pull_request_files",
                response=response,
            )
            try:
                chunk: object = response.json()
            except ValueError as exc:
                msg = "GitHub files response was not valid JSON"
                raise GitHubRestError(
                    msg,
                    status_code=response.status_code,
                    retriable=False,
                    request_id=response.headers.get("x-github-request-id"),
                ) from exc
            if not isinstance(chunk, list):
                msg = "GitHub files response JSON was not an array"
                raise GitHubRestError(
                    msg,
                    status_code=response.status_code,
                    retriable=False,
                    request_id=response.headers.get("x-github-request-id"),
                )
            for item in chunk:
                if not isinstance(item, dict):
                    msg = "GitHub files response contained non-object entries"
                    raise GitHubRestError(
                        msg,
                        status_code=response.status_code,
                        retriable=False,
                        request_id=response.headers.get("x-github-request-id"),
                    )
                aggregated.append(item)
            if len(chunk) < _FILES_PER_PAGE:
                break
            page += 1
            if page > _MAX_FILE_PAGES:
                msg = "pull request files pagination exceeded internal safety limit"
                raise GitHubRestError(
                    msg,
                    status_code=response.status_code,
                    retriable=False,
                    request_id=response.headers.get("x-github-request-id"),
                )
    finally:
        if owns_client:
            client.close()

    return aggregated


def fetch_repository_text_file_contents(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    path: str,
    git_ref: str,
    http_client: httpx.Client | None = None,
) -> str | None:
    """Return UTF-8 file text from the contents API, or ``None`` when missing (404).

    Calls ``GET /repos/{owner}/{repo}/contents/{path}?ref={git_ref}`` per
    ``docs/DESIGN.md`` §13.5. Only ``type=file`` responses with ``base64``
    encoding are decoded; other successful shapes are treated as missing.
    """

    own = _validate_repo_segment("owner", owner)
    rep = _validate_repo_segment("repository", repo)
    cleaned_path = path.strip().lstrip("/")
    if not cleaned_path or ".." in cleaned_path.split("/"):
        msg = "path must be a non-empty repo-relative path without parent segments"
        raise ValueError(msg)
    ref = git_ref.strip()
    if not ref:
        msg = "git_ref must be a non-empty branch or tag name"
        raise ValueError(msg)

    url = (
        f"{_GITHUB_API_ORIGIN}/repos/"
        f"{quote(own, safe='')}/{quote(rep, safe='')}/contents/{quote(cleaned_path, safe='')}"
    )
    headers = _installation_auth_headers(installation_token)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=_DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        try:
            response = client.get(url, headers=headers, params={"ref": ref})
        except httpx.HTTPError as exc:
            _log_github_failure(
                operation="fetch_repository_text_file_contents",
                response=None,
                retriable=True,
                detail=str(exc),
            )
            msg = "HTTP transport error while fetching repository file contents"
            raise GitHubRestError(
                msg,
                status_code=None,
                retriable=True,
                request_id=None,
            ) from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            return None

        _raise_for_github_response(
            operation="fetch_repository_text_file_contents",
            response=response,
        )
    finally:
        if owns_client:
            client.close()

    try:
        body: object = response.json()
    except ValueError as exc:
        msg = "GitHub contents response was not valid JSON"
        raise GitHubRestError(
            msg,
            status_code=response.status_code,
            retriable=False,
            request_id=response.headers.get("x-github-request-id"),
        ) from exc
    if not isinstance(body, dict):
        msg = "GitHub contents response JSON was not an object"
        raise GitHubRestError(
            msg,
            status_code=response.status_code,
            retriable=False,
            request_id=response.headers.get("x-github-request-id"),
        )
    if body.get("type") != "file":
        return None
    if body.get("encoding") != "base64" or not isinstance(body.get("content"), str):
        return None
    raw_b64 = str(body["content"]).replace("\n", "")
    try:
        return base64.b64decode(raw_b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        msg = "GitHub contents payload was not valid base64 UTF-8 text"
        raise GitHubRestError(
            msg,
            status_code=response.status_code,
            retriable=False,
            request_id=response.headers.get("x-github-request-id"),
        ) from exc
