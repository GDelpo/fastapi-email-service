"""Suppression list schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import SuppressionReason
from app.schemas.common import CamelModel


class SuppressionCreate(BaseModel):
    """Create a suppression entry manually."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    reason: SuppressionReason = SuppressionReason.MANUAL
    description: str | None = Field(default=None, max_length=500)


class SuppressionResponse(CamelModel):
    """Suppression entry response."""

    id: UUID
    email: str
    reason: SuppressionReason
    source: str
    description: str | None = None
    provider_message: str | None = None
    related_email_id: UUID | None = None
    expires_at: datetime | None = None
    is_active: bool
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class SuppressionCheck(CamelModel):
    """Result of checking if an email is suppressed."""

    email: str
    is_suppressed: bool
    reason: SuppressionReason | None = None
    description: str | None = None
    suppressed_at: datetime | None = None


class SuppressionStats(CamelModel):
    """Suppression list statistics."""

    total: int
    active: int
    by_reason: dict[str, int] = Field(default_factory=dict)
