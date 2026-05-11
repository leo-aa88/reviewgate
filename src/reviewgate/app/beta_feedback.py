"""``POST /api/beta-feedback`` private beta feedback (issue #55)."""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.models import BetaFeedback

router = APIRouter(prefix="/api", tags=["api"])

_MAX_MESSAGE_CHARS: Final[int] = 8000
_MAX_CONTACT_CHARS: Final[int] = 500


class BetaFeedbackRequest(BaseModel):
    """JSON body for beta feedback capture."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        ...,
        max_length=_MAX_MESSAGE_CHARS,
        description="Free-text feedback.",
    )
    contact: str | None = Field(
        default=None,
        max_length=_MAX_CONTACT_CHARS,
        description="Optional email or handle for follow-up.",
    )

    @field_validator("message")
    @classmethod
    def message_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "message is required"
            raise ValueError(msg)
        return stripped

    @field_validator("contact")
    @classmethod
    def contact_stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


def persist_beta_feedback(settings: AppSettings, payload: BetaFeedbackRequest) -> None:
    """Insert a ``beta_feedback`` row.

    Args:
        settings: Application settings (requires ``REVIEWGATE_DATABASE_URL``).
        payload: Validated request body.

    Raises:
        RuntimeError: When no database engine can be constructed.
        OperationalError: When Postgres is unreachable.
    """

    engine = create_engine_from_settings(settings)
    if engine is None:
        msg = "persist_beta_feedback requires REVIEWGATE_DATABASE_URL"
        raise RuntimeError(msg)

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        _insert_beta_feedback_row(session, payload)
        session.commit()


def _insert_beta_feedback_row(session: Session, payload: BetaFeedbackRequest) -> None:
    row = BetaFeedback(message=payload.message, contact=payload.contact)
    session.add(row)


def get_app_settings() -> AppSettings:
    """FastAPI dependency returning fresh :class:`AppSettings`."""

    return AppSettings()


@router.post("/beta-feedback")
def create_beta_feedback(
    payload: BetaFeedbackRequest,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict[str, bool]:
    """Persist feedback and return a minimal acknowledgment."""

    try:
        persist_beta_feedback(settings, payload)
    except RuntimeError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database URL is required for beta feedback",
        ) from None
    except OperationalError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Temporary database error while saving beta feedback",
        ) from exc

    return {"ok": True}
