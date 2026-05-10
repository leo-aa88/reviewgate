"""``pr_metadata_hash`` inputs (``docs/DESIGN.md`` §13.6; issue #42).

Pure normalization and hashing so workers can build cache keys without
calling GitHub. Linked issue references use the same detectors as
:func:`reviewgate.core.linked_issue.find_issue_references` on normalized text.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Final

from reviewgate.core.linked_issue import find_issue_references

_HTML_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"<!--.*?-->", flags=re.DOTALL)


def normalize_text_for_pr_metadata_hash(value: str | None) -> str:
    """Apply §13.6 normalization (LF, trim, whitespace collapse, strip HTML comments)."""

    if value is None:
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _HTML_COMMENT_RE.sub("", text)
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def build_pr_metadata_hash_payload(
    *,
    title: str | None,
    body: str | None,
    base_branch: str | None,
) -> dict[str, object]:
    """Return the canonical JSON-shaped payload hashed for ``pr_metadata_hash``."""

    norm_title = normalize_text_for_pr_metadata_hash(title)
    norm_body = normalize_text_for_pr_metadata_hash(body)
    norm_base = normalize_text_for_pr_metadata_hash(base_branch)
    refs = sorted(find_issue_references(norm_title, norm_body))
    return {
        "base_branch": norm_base,
        "body": norm_body,
        "linked_issue_refs": refs,
        "title": norm_title,
    }


def compute_pr_metadata_hash(
    *,
    title: str | None,
    body: str | None,
    base_branch: str | None,
) -> str:
    """Return a stable SHA-256 hex digest over §13.6 fields."""

    payload = build_pr_metadata_hash_payload(
        title=title,
        body=body,
        base_branch=base_branch,
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
