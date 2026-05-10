"""``POST /api/beta-leads`` (``docs/DESIGN.md`` §17.3; issue #39)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from reviewgate.app.settings import AppSettings
from reviewgate.app.storage.db import create_engine_from_settings, create_session_factory
from reviewgate.app.storage.models import BetaLead

router = APIRouter(prefix="/api", tags=["api"])


class BetaLeadRequest(BaseModel):
    """JSON body for §17.3 beta lead capture."""

    model_config = ConfigDict(extra="forbid")

    email: str
    name: str | None = None
    company: str | None = None
    role: str | None = None
    github_org: str | None = None
    team_size: str | None = None
    source: str | None = Field(
        default="landing",
        description="Attribution source (for example ``landing``).",
    )

    @field_validator("email")
    @classmethod
    def email_must_look_like_email(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "email is required"
            raise ValueError(msg)
        if "@" not in stripped or "." not in stripped.split("@")[-1]:
            msg = "email must contain a domain"
            raise ValueError(msg)
        return stripped


def _optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def persist_beta_lead(settings: AppSettings, payload: BetaLeadRequest) -> None:
    """Insert a ``beta_leads`` row.

    Args:
        settings: Application settings (requires ``REVIEWGATE_DATABASE_URL``).
        payload: Validated request body.

    Raises:
        RuntimeError: When no database engine can be constructed.
        OperationalError: When Postgres is unreachable.
    """

    engine = create_engine_from_settings(settings)
    if engine is None:
        msg = "persist_beta_lead requires REVIEWGATE_DATABASE_URL"
        raise RuntimeError(msg)

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        _insert_beta_lead_row(session, payload)
        session.commit()


def _insert_beta_lead_row(session: Session, payload: BetaLeadRequest) -> None:
    row = BetaLead(
        email=payload.email,
        name=_optional_str(payload.name),
        company=_optional_str(payload.company),
        role=_optional_str(payload.role),
        github_org=_optional_str(payload.github_org),
        team_size=_optional_str(payload.team_size),
        source=_optional_str(payload.source),
    )
    session.add(row)


def get_app_settings() -> AppSettings:
    """FastAPI dependency returning fresh :class:`AppSettings`."""

    return AppSettings()


@router.post("/beta-leads")
def create_beta_lead(
    payload: BetaLeadRequest,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict[str, bool]:
    """Persist a beta signup row and return the §17.3 acknowledgment."""

    try:
        persist_beta_lead(settings, payload)
    except RuntimeError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database URL is required for beta lead capture",
        ) from None
    except OperationalError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Temporary database error while saving beta lead",
        ) from exc

    return {"ok": True}
