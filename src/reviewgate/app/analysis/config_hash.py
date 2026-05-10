"""``config_hash`` for hosted analysis (``docs/DESIGN.md`` §13.5–§13.6; issue #43).

Fetches ``.reviewgate.yml`` from the repository at a caller-supplied ref (PR
``base`` branch) via the GitHub contents API, then runs
:func:`reviewgate.core.config.load_config`
so malformed YAML still yields defaults plus §12 warnings. The digest covers the
effective :class:`~reviewgate.core.config.ReviewGateConfig` JSON only.
"""

from __future__ import annotations

import hashlib
import json

import httpx
from pydantic import SecretStr

from reviewgate.app.github.client import fetch_repository_text_file_contents
from reviewgate.core.config import DEFAULT_CONFIG_PATH, ConfigLoadResult, load_config


def compute_config_hash_from_yaml(yaml_text: str | None) -> tuple[str, ConfigLoadResult]:
    """Parse YAML (or missing file) and return ``(sha256_hex, load_result)``."""

    result = load_config(yaml_text)
    payload = result.config.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest, result


def fetch_reviewgate_yml_and_config_hash(
    installation_token: SecretStr,
    *,
    owner: str,
    repo: str,
    base_ref: str,
    http_client: httpx.Client | None = None,
) -> tuple[str, ConfigLoadResult]:
    """Fetch ``.reviewgate.yml`` at ``base_ref`` and compute the stable ``config_hash``."""

    ref = base_ref.strip()
    if not ref:
        msg = "base_ref must be a non-empty branch or tag name"
        raise ValueError(msg)
    raw = fetch_repository_text_file_contents(
        installation_token,
        owner=owner,
        repo=repo,
        path=DEFAULT_CONFIG_PATH,
        git_ref=ref,
        http_client=http_client,
    )
    return compute_config_hash_from_yaml(raw)
