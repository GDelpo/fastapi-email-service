"""Email schemas: recipients, attachments, events, email CRUD, queue stats."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import (
    BounceType,
    EmailPriority,
    EmailStatus,
    EmailType,
    EventType,
)
from app.schemas.common import CamelModel


# ============================================================================
# Recipient Schemas
# ============================================================================


class EmailRecipient(BaseModel):
    """Email recipient with optional name."""

    email: EmailStr
    name: str | None = None


# ============================================================================
# Attachment Schemas
# ============================================================================


class AttachmentCreate(BaseModel):
    """Attachment in email request."""

    filename: str = Field(..., max_length=255)
    content_type: str = Field(..., max_length=100)
    content_base64: str  # Base64 encoded content
    is_inline: bool = False
    content_id: str | None = None  # For inline images (cid:)


class AttachmentResponse(CamelModel):
    """Attachment response."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    storage_path: str | None = None
    is_inline: bool
    content_id: str | None = None
    created_at: datetime


# ============================================================================
# Email Event Schemas
# ============================================================================


class EmailEventResponse(CamelModel):
    """Email event for timeline."""

    id: UUID
    event_type: EventType
    timestamp: datetime
    description: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    link_url: str | None = None


# ============================================================================
# Email Schemas
# ============================================================================


class EmailCreate(BaseModel):
    """Create email request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # Recipients
    to: list[EmailRecipient] = Field(..., min_length=1, max_length=50)
    cc: list[EmailRecipient] | None = None
    bcc: list[EmailRecipient] | None = None
    reply_to: EmailStr | None = None

    # Sender (optional - uses default if not provided)
    from_email: EmailStr | None = None
    from_name: str | None = None

    # Content (either direct content or template)
    subject: str | None = Field(default=None, max_length=500)
    html_content: str | None = None
    text_content: str | None = None

    # Or use a template
    template_slug: str | None = None
    template_variables: dict[str, Any] | None = None

    # Attachments
    attachments: list[AttachmentCreate] | None = None

    # Options
    priority: EmailPriority = EmailPriority.NORMAL
    email_type: EmailType = EmailType.TRANSACTIONAL
    scheduled_at: datetime | None = None

    # Tracking (source_service is derived from auth token)
    source_request_id: str | None = Field(default=None, max_length=100)


class EmailUpdate(BaseModel):
    """Update email request (limited fields)."""

    priority: EmailPriority | None = None
    scheduled_at: datetime | None = None


def _parse_recipients(value: Any) -> list[dict]:
    """Parse recipients from string or list."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return value


class EmailResponse(CamelModel):
    """Full email response."""

    id: UUID

    # Addressing
    from_email: str
    from_name: str | None
    to_emails: list[EmailRecipient] = []
    cc_emails: list[EmailRecipient] | None = None
    bcc_emails: list[EmailRecipient] | None = None
    reply_to: str | None

    # Content
    subject: str
    html_content: str | None
    text_content: str | None

    @field_validator("to_emails", "cc_emails", "bcc_emails", mode="before")
    @classmethod
    def parse_recipients_json(cls, v: Any) -> list[dict] | None:
        """Parse JSON string recipients to list."""
        if v is None:
            return None
        return _parse_recipients(v)

    template_id: UUID | None

    # Status
    status: EmailStatus
    priority: EmailPriority
    email_type: EmailType

    # Provider
    provider: str | None
    provider_message_id: str | None

    # Retry info
    retry_count: int
    max_retries: int
    next_retry_at: datetime | None
    last_error: str | None

    # Tracking
    scheduled_at: datetime | None
    sent_at: datetime | None
    delivered_at: datetime | None
    opened_at: datetime | None
    clicked_at: datetime | None
    bounced_at: datetime | None
    bounce_type: BounceType | None
    bounce_reason: str | None

    # Origin
    source_service: str | None
    source_request_id: str | None
    owner_id: UUID | None

    # Timestamps
    created_at: datetime
    updated_at: datetime

    # Related
    attachments: list[AttachmentResponse] = []
    events: list[EmailEventResponse] = []


class EmailSummary(CamelModel):
    """Email summary for list responses."""

    id: UUID
    from_email: str
    to_emails: list[EmailRecipient] = []
    subject: str
    status: EmailStatus
    priority: EmailPriority
    email_type: EmailType
    provider: str | None
    retry_count: int
    source_service: str | None
    created_at: datetime
    sent_at: datetime | None
    scheduled_at: datetime | None

    @field_validator("to_emails", mode="before")
    @classmethod
    def parse_recipients_json(cls, v: Any) -> list[dict]:
        """Parse JSON string recipients to list."""
        return _parse_recipients(v)


# ============================================================================
# Queue/Processing
# ============================================================================


class QueueStats(CamelModel):
    """Queue statistics."""

    queued: int
    processing: int
    pending_retry: int
    failed_today: int
    sent_today: int


class ProcessPendingResponse(CamelModel):
    """Response from processing pending emails."""

    processed: int
    sent: int
    failed: int
    errors: list[str] = []
