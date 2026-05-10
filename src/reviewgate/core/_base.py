"""Shared Pydantic base for engine contract models.

Centralizes the strict JSON-contract defaults (`extra="forbid"`, `strict=True`,
`str_strip_whitespace=True`) so every model in `reviewgate.core` rejects
unknown keys, refuses silent type coercion, and trims surrounding whitespace
from string inputs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base class for every engine contract model.

    Subclass this instead of :class:`pydantic.BaseModel` so the strict
    JSON-contract defaults stay defined in exactly one place.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


__all__ = ["StrictModel"]
