"""Bulk email schemas."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import EmailPriority, EmailType
from app.schemas.common import CamelModel


class BulkRecipient(BaseModel):
    """Single recipient for bulk email with optional personalization."""

    email: EmailStr
    name: str | None = None
    variables: dict[str, Any] | None = None  # Template variables for this recipient
    # Per-recipient overrides: when set, take precedence over batch-level html_content/subject
    html_content: str | None = None
    subject: str | None = None
    # Caller-supplied ID for correlation in queuedDetails/skippedDetails response
    external_id: str | None = None


class BulkEmailCreate(BaseModel):
    """Create bulk email request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # Batch name (optional, for identification)
    name: str | None = Field(default=None, max_length=255)

    # Content: EITHER template_id OR (subject + html_content) OR per-recipient on every recipient
    template_id: UUID | None = None
    subject: str | None = Field(default=None, max_length=500)
    html_content: str | None = None
    text_content: str | None = None

    # Sender
    from_email: EmailStr | None = None
    from_name: str | None = None
    reply_to: EmailStr | None = None

    # Recipients (min 1, max configurable - default 1000)
    recipients: list[BulkRecipient] = Field(..., min_length=1, max_length=1000)

    # Options
    priority: EmailPriority = EmailPriority.LOW
    email_type: EmailType = EmailType.MARKETING
    skip_suppressed: bool = True  # True = skip suppressed, False = fail entire batch

    def model_post_init(self, __context: Any) -> None:
        """Validate content source.

        Either template_id OR batch-level (subject + html_content) must be provided,
        OR every recipient must supply their own html_content and subject.
        """
        has_template = self.template_id is not None
        has_batch_content = self.subject is not None and self.html_content is not None
        all_recipients_have_content = all(
            r.html_content is not None and r.subject is not None for r in self.recipients
        )

        if not has_template and not has_batch_content and not all_recipients_have_content:
            raise ValueError(
                "Must provide either 'template_id', both 'subject' and 'html_content' "
                "at batch level, or 'html_content' and 'subject' on every recipient."
            )
        if has_template and (has_batch_content or all_recipients_have_content):
            raise ValueError("Provide either 'template_id' OR content fields, not both")


class SkippedRecipient(CamelModel):
    """Details of a skipped recipient."""

    email: str
    reason: str  # "suppressed", "invalid_email", "duplicate", "creation_error"
    details: str | None = None
    external_id: str | None = None  # Echoes caller's external_id for easy correlation


class QueuedRecipient(CamelModel):
    """Details of a successfully queued recipient."""

    email: str
    message_id: str  # Email record ID in email-service (UUID)
    external_id: str | None = None  # Echoes caller's external_id for easy correlation


class BulkEmailResponse(CamelModel):
    """Response for bulk email creation."""

    batch_id: UUID
    status: str
    name: str | None = None
    total_recipients: int
    queued: int
    skipped: int
    skipped_details: list[SkippedRecipient] = []
    queued_details: list[QueuedRecipient] = []  # Per-recipient message IDs for tracking
    created_at: datetime


class BatchSummary(CamelModel):
    """Summary of a batch for listing."""

    id: UUID
    name: str | None
    status: str
    total_recipients: int
    queued_count: int
    sent_count: int
    failed_count: int
    skipped_count: int
    created_at: datetime
    completed_at: datetime | None


class BatchDetailResponse(CamelModel):
    """Detailed batch response with stats."""

    id: UUID
    name: str | None
    status: str

    # Content info
    template_id: UUID | None
    subject_template: str | None
    from_email: str
    from_name: str | None

    # Stats
    total_recipients: int
    queued_count: int
    sent_count: int
    failed_count: int
    skipped_count: int
    skipped_details: list[SkippedRecipient] = []

    # Options
    priority: EmailPriority
    email_type: EmailType

    # Timestamps
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @field_validator("skipped_details", mode="before")
    @classmethod
    def parse_skipped_json(cls, v: Any) -> list[dict]:
        """Parse JSON string to list."""
        if v is None:
            return []
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return []
        return v
