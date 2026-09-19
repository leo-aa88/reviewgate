"""Read a trusted base-branch PR template in the Action wrapper."""

from __future__ import annotations

import base64
import binascii
import urllib.request
from urllib.parse import quote

from .fetch_pr import _http_get_json, _split_repo

_TEMPLATE_PATH = ".github/PULL_REQUEST_TEMPLATE.md"


def fetch_pr_template(
    *,
    token: str,
    repo_slug: str,
    base_ref: str,
    opener: urllib.request.OpenerDirector | None = None,
) -> str | None:
    """Fetch a PR template from the target branch.

    Args:
        token: GitHub token with contents-read permission.
        repo_slug: Repository in owner/repo format.
        base_ref: PR target branch; never the PR head.
        opener: Injectable HTTP opener for tests.

    Returns:
        UTF-8 template text or None on 404.

    Raises:
        RuntimeError: For invalid inputs, GitHub errors other than 404,
            malformed response data or invalid template encoding.
    """

    owner, repo = _split_repo(repo_slug)
    ref = base_ref.strip()
    if not ref:
        raise RuntimeError("PR base branch is required to fetch its template")

    url = (
        f"https://api.github.com/repos/{quote(owner, safe='')}/"
        f"{quote(repo, safe='')}/contents/{_TEMPLATE_PATH}"
        f"?ref={quote(ref, safe='')}"
    )
    payload, _headers = _http_get_json(url, token=token, opener=opener, missing_ok=True)
    if payload is None:
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "file"
        or payload.get("encoding") != "base64"
        or not isinstance(payload.get("content"), str)
    ):
        raise RuntimeError("invalid GitHub PR template contents response")

    try:
        encoded = payload["content"].replace("\n", "")
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise RuntimeError("GitHub PR template was not valid base64 UTF-8") from exc


__all__ = ["fetch_pr_template"]
