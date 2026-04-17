"""Template schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import EmailType
from app.schemas.common import CamelModel


class TemplateCreate(BaseModel):
    """Create template request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100, pattern=r"^[a-z0-9-]+$")
    description: str | None = Field(default=None, max_length=500)
    subject_template: str = Field(..., max_length=500)
    html_template: str
    text_template: str | None = None
    variables: list[str] | None = None
    email_type: EmailType = EmailType.TRANSACTIONAL


class TemplateUpdate(BaseModel):
    """Update template request."""

    name: str | None = None
    description: str | None = None
    subject_template: str | None = None
    html_template: str | None = None
    text_template: str | None = None
    variables: list[str] | None = None
    is_active: bool | None = None


class TemplateResponse(CamelModel):
    """Template response."""

    id: UUID
    name: str
    slug: str
    description: str | None
    subject_template: str
    html_template: str
    text_template: str | None
    variables: list[str] | None = None
    email_type: EmailType
    is_active: bool
    created_at: datetime
    updated_at: datetime
