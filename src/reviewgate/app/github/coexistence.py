"""Hosted App vs Action coexistence (``docs/DESIGN.md`` §14.1; issue #54).

``.reviewgate.yml`` ``mode`` controls whether the hosted worker posts GitHub
comments, labels, and check runs alongside (or instead of) the open-source
Action.

Example:
    When ``mode`` is ``action``, skip hosted posting entirely::

        from reviewgate.core.config import ReviewGateConfig

        from reviewgate.app.github.coexistence import hosted_github_outputs_enabled

        assert hosted_github_outputs_enabled(ReviewGateConfig(mode="action")) is False
"""

from __future__ import annotations

from reviewgate.core.config import (
    DEFAULT_STATUS_CHECK_NAME,
    ReviewGateConfig,
    StatusCheck,
)


def hosted_github_outputs_enabled(cfg: ReviewGateConfig) -> bool:
    """Return ``True`` when the hosted worker may post PR feedback to GitHub.

    ``mode: action`` means the GitHub Action owns comments/checks; the hosted
    App must not post (§14.1).

    Args:
        cfg: Effective repository configuration (including ``mode``).

    Returns:
        ``False`` only for ``mode == "action"``.
    """

    return cfg.mode != "action"


def effective_hosted_status_check(cfg: ReviewGateConfig) -> StatusCheck:
    """Return the :class:`~reviewgate.core.config.StatusCheck` to use on the host.

    For ``mode: both``, GitHub requires a **distinct** check name from the
    Action's default. When the repo still uses :data:`DEFAULT_STATUS_CHECK_NAME`,
    this appends ``" (hosted)"`` so branch protection can target either check.

    Custom check names are returned unchanged; operators using ``both`` should
    ensure Action and App names do not collide.

    Args:
        cfg: Effective configuration.

    Returns:
        Possibly adjusted :class:`~reviewgate.core.config.StatusCheck` copy.
    """

    base = cfg.status_check
    if cfg.mode != "both":
        return base
    if base.name.strip() == DEFAULT_STATUS_CHECK_NAME:
        return base.model_copy(
            update={"name": f"{DEFAULT_STATUS_CHECK_NAME} (hosted)"},
        )
    return base
