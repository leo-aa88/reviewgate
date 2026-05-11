"""Redis-backed internal rate limits for the hosted app (``docs/DESIGN.md`` §22.2)."""

from __future__ import annotations

from reviewgate.app.rate_limit.limiter import check_analysis_rate_limits

__all__ = ["check_analysis_rate_limits"]
