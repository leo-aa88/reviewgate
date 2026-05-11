"""Minimal LLM input packaging (``docs/DESIGN.md`` §11.5; issue #62)."""

from __future__ import annotations

import json
from typing import Any

from reviewgate.core.schemas import ChangedFile, PRRecord, ReviewabilityReport

from reviewgate.app.llm.budgets import (
    LlmInputPackaging,
    input_token_target_for_pr,
    rough_token_estimate,
    truncate_to_token_budget,
)

_AVOID_CATEGORIES: frozenset[str] = frozenset(
    {"generated", "lockfile", "snapshot", "minified", "vendored"},
)


def _compact_files_for_llm(
    files: list[ChangedFile],
    *,
    mode: LlmInputPackaging,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in files:
        row: dict[str, Any] = {
            "filename": f.filename,
            "status": f.status,
            "additions": f.additions,
            "deletions": f.deletions,
        }
        if mode == "full":
            row["changes"] = f.changes
        rows.append(row)
    return rows


def _category_summary(report: ReviewabilityReport) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in report.file_categories:
        cats = [c for c in row.categories if c not in _AVOID_CATEGORIES]
        if not cats and any(c in _AVOID_CATEGORIES for c in row.categories):
            out.append(
                {
                    "filename": row.filename,
                    "note": "generated_or_lockfile_like",
                    "risky": row.risky,
                },
            )
            continue
        out.append(
            {
                "filename": row.filename,
                "categories": list(row.categories),
                "risky": row.risky,
                "changes": row.changes,
            },
        )
    return out


def build_llm_user_message(
    *,
    pr: PRRecord,
    report: ReviewabilityReport,
    files: list[ChangedFile],
    mode: LlmInputPackaging,
) -> str:
    """Build the user message JSON blob sent to the LLM (§11.5 allow-list)."""

    target_tokens = input_token_target_for_pr(pr.changed_files)
    if mode == "summary_only":
        target_tokens = min(target_tokens, 4_000)

    body_text = str(pr.body)
    files_payload = _compact_files_for_llm(files, mode=mode)
    warnings_payload = [w.model_dump(mode="json") for w in report.warnings]
    category_payload = _category_summary(report)

    for _ in range(32):
        payload = {
            "pr_title": pr.title,
            "pr_body": body_text,
            "deterministic_reviewability": report.reviewability,
            "stats": report.stats,
            "deterministic_warnings": warnings_payload,
            "file_category_summary": category_payload,
            "compact_changed_files": files_payload,
        }
        if mode == "summary_only":
            payload["note"] = (
                "Large PR: summary-only packaging; no full patches; "
                "lockfile/generated detail omitted per §11.5."
            )
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if rough_token_estimate(raw) <= target_tokens:
            return raw

        if len(body_text) > 200:
            body_text = truncate_to_token_budget(
                body_text,
                max(50, rough_token_estimate(body_text) // 2),
            )
            continue
        if len(warnings_payload) > 1:
            warnings_payload = warnings_payload[: max(1, len(warnings_payload) // 2)]
            continue
        if len(category_payload) > 1:
            category_payload = category_payload[: max(1, len(category_payload) // 2)]
            continue
        if len(files_payload) > 1:
            files_payload = files_payload[: max(1, len(files_payload) // 2)]
            continue
        body_text = truncate_to_token_budget(body_text, max(1, target_tokens // 4))
        if len(body_text) < 20:
            break

    payload = {
        "pr_title": pr.title,
        "pr_body": body_text,
        "deterministic_reviewability": report.reviewability,
        "stats": report.stats,
        "deterministic_warnings": warnings_payload,
        "file_category_summary": category_payload,
        "compact_changed_files": files_payload,
    }
    if mode == "summary_only":
        payload["note"] = (
            "Large PR: summary-only packaging; no full patches; "
            "lockfile/generated detail omitted per §11.5."
        )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
