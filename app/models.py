"""
Database Models - Email Service

Complete model structure for enterprise-grade email tracking.
"""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    """Get current UTC time with timezone info."""
    return datetime.now(UTC)


# ============================================================================
# Enums
# ============================================================================


class EmailStatus(str, Enum):
    """Email lifecycle status."""

    DRAFT = "draft"  # Created but not yet queued
    QUEUED = "queued"  # In queue waiting to be processed
    PROCESSING = "processing"  # Currently being sent
    SENT = "sent"  # Sent to provider successfully
    DELIVERED = "delivered"  # Confirmed delivered by provider
    BOUNCED = "bounced"  # Bounced (hard or soft)
    FAILED = "failed"  # Failed to send (will retry)
    REJECTED = "rejected"  # Rejected (spam, invalid, etc.)
    OPENED = "opened"  # Recipient opened the email
    CLICKED = "clicked"  # Recipient clicked a link
    CANCELLED = "cancelled"  # Cancelled before sending


class EmailPriority(str, Enum):
    """Email priority for queue ordering."""

    CRITICAL = "critical"  # Password reset, verification, 2FA - immediate
    HIGH = "high"  # Important notifications
    NORMAL = "normal"  # Regular emails
    LOW = "low"  # Newsletters, marketing


class EmailType(str, Enum):
    """Type of email for categorization."""

    TRANSACTIONAL = "transactional"  # Password reset, verification, etc.
    NOTIFICATION = "notification"  # Alerts, updates
    MARKETING = "marketing"  # Newsletters, promotions
    SYSTEM = "system"  # Internal system emails


class EventType(str, Enum):
    """Types of email events for tracking."""

    CREATED = "created"
    QUEUED = "queued"
    PROCESSING = "processing"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    SOFT_BOUNCE = "soft_bounce"
    HARD_BOUNCE = "hard_bounce"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    OPENED = "opened"
    CLICKED = "clicked"
    UNSUBSCRIBED = "unsubscribed"
    SPAM_REPORT = "spam_report"
    CANCELLED = "cancelled"


class BounceType(str, Enum):
    """Type of bounce."""

    SOFT = "soft"  # Temporary (mailbox full, server down)
    HARD = "hard"  # Permanent (invalid address)


class SuppressionReason(str, Enum):
    """Reason for email suppression."""

    HARD_BOUNCE = "hard_bounce"  # Invalid email address
    SOFT_BOUNCE = "soft_bounce"  # Temporary issue (may expire)
    SPAM_REPORT = "spam_report"  # User reported as spam
    UNSUBSCRIBE = "unsubscribe"  # User unsubscribed
    MANUAL = "manual"  # Manually added by admin
    INVALID = "invalid"  # Invalid email format
    BLOCKED = "blocked"  # Blocked by provider


# ============================================================================
# Models
# ============================================================================


class BatchStatus(str, Enum):
    """Status of a bulk email batch."""

    PENDING = "pending"  # Created, not yet processed
    PROCESSING = "processing"  # Currently creating individual emails
    QUEUED = "queued"  # All emails queued for sending
    PARTIAL = "partial"  # Some emails failed validation
    COMPLETED = "completed"  # All emails processed (sent/failed)
    FAILED = "failed"  # Batch creation failed


class EmailBatch(SQLModel, table=True):
    """Batch for bulk email sending - groups multiple emails from one request."""

    __tablename__ = "email_batches"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Batch metadata
    name: str | None = Field(default=None, max_length=255)
    status: BatchStatus = Field(default=BatchStatus.PENDING, index=True)

    # Content source (either template_id or inline content)
    template_id: UUID | None = Field(default=None, foreign_key="email_templates.id")
    subject_template: str | None = Field(default=None, max_length=500)
    html_template: str | None = Field(default=None)
    text_template: str | None = Field(default=None)

    # Sender
    from_email: str = Field(max_length=255)
    from_name: str | None = Field(default=None, max_length=255)
    reply_to: str | None = Field(default=None, max_length=255)

    # Options
    priority: EmailPriority = Field(default=EmailPriority.NORMAL)
    email_type: EmailType = Field(default=EmailType.MARKETING)
    skip_suppressed: bool = Field(default=True)
    track_opens: bool = Field(default=True)
    track_clicks: bool = Field(default=True)

    # Stats
    total_recipients: int = Field(default=0)
    queued_count: int = Field(default=0)
    sent_count: int = Field(default=0)
    failed_count: int = Field(default=0)
    skipped_count: int = Field(default=0)

    # Skipped recipients info (JSON array)
    skipped_details: str | None = Field(default=None)

    # Origin tracking
    source_service: str | None = Field(default=None, max_length=100, index=True)
    owner_id: UUID | None = Field(default=None, index=True)

    # Timestamps
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False, index=True),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
    )


class EmailSuppression(SQLModel, table=True):
    """Suppression list - emails that should not receive messages.

    Populated by:
    - Webhooks (bounce, spam report, unsubscribe)
    - Manual admin action
    - Email validation failures
    """

    __tablename__ = "email_suppressions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # The suppressed email address (normalized to lowercase)
    email: str = Field(
        sa_column=sa.Column(sa.String(255), unique=True, index=True, nullable=False),
    )

    # Why it was suppressed
    reason: SuppressionReason = Field(
        sa_column=sa.Column(sa.Enum(SuppressionReason, name="suppressionreason"), nullable=False)
    )

    # Where the suppression came from
    source: str = Field(max_length=50)  # sendgrid, mailchimp, smtp, manual, etc.

    # Additional context
    description: str | None = Field(default=None, max_length=500)

    # Original bounce/error message from provider
    provider_message: str | None = Field(default=None, max_length=1000)

    # Related email ID (if suppression came from a specific send attempt)
    related_email_id: UUID | None = Field(default=None, index=True)

    # Expiration (for soft bounces that might recover)
    expires_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True, index=True),
    )

    # Is this suppression active?
    is_active: bool = Field(default=True, index=True)

    # Who added it (for manual suppressions)
    created_by: str | None = Field(default=None, max_length=100)

    # Timestamps
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False, index=True),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )


class EmailTemplate(SQLModel, table=True):
    """Email templates for reusable content."""

    __tablename__ = "email_templates"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Template identification
    name: str = Field(index=True, max_length=100)
    slug: str = Field(unique=True, index=True, max_length=100)
    description: str | None = Field(default=None, max_length=500)

    # Content
    subject_template: str = Field(max_length=500)
    html_template: str
    text_template: str | None = Field(default=None)

    # Metadata
    variables: str | None = Field(default=None)  # JSON list of variable names
    email_type: EmailType = Field(default=EmailType.TRANSACTIONAL)

    # Audit
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )
    created_by: UUID | None = Field(default=None)


class Email(SQLModel, table=True):
    """Main email record."""

    __tablename__ = "emails"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Addressing
    from_email: str = Field(max_length=255, index=True)
    from_name: str | None = Field(default=None, max_length=255)
    to_emails: str  # JSON array of {email, name}
    cc_emails: str | None = Field(default=None)  # JSON array
    bcc_emails: str | None = Field(default=None)  # JSON array
    reply_to: str | None = Field(default=None, max_length=255)

    # Content
    subject: str = Field(max_length=500)
    html_content: str | None = Field(default=None)
    text_content: str | None = Field(default=None)

    # Template (optional)
    template_id: UUID | None = Field(default=None, foreign_key="email_templates.id")
    template_variables: str | None = Field(default=None)  # JSON object

    # Status and tracking
    status: EmailStatus = Field(default=EmailStatus.QUEUED, index=True)
    priority: EmailPriority = Field(default=EmailPriority.NORMAL, index=True)
    email_type: EmailType = Field(default=EmailType.TRANSACTIONAL)

    # Provider info
    provider: str | None = Field(default=None, max_length=50)
    provider_message_id: str | None = Field(default=None, max_length=255, index=True)

    # Retry logic
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)
    next_retry_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True, index=True),
    )
    last_error: str | None = Field(default=None)

    # Scheduling
    scheduled_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True, index=True),
    )

    # Tracking timestamps
    sent_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
    )
    delivered_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
    )
    opened_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
    )
    clicked_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
    )
    bounced_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
    )
    bounce_type: BounceType | None = Field(default=None)
    bounce_reason: str | None = Field(default=None)

    # Batch (for bulk sends)
    batch_id: UUID | None = Field(default=None, foreign_key="email_batches.id", index=True)

    # Origin tracking
    source_service: str | None = Field(default=None, max_length=100, index=True)
    source_request_id: str | None = Field(default=None, max_length=100)
    owner_id: UUID | None = Field(default=None, index=True)

    # Timestamps
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False, index=True),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )

    # Relationships
    events: list["EmailEvent"] = Relationship(back_populates="email")
    attachments: list["EmailAttachment"] = Relationship(back_populates="email")


class EmailEvent(SQLModel, table=True):
    """Event log for email lifecycle tracking."""

    __tablename__ = "email_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email_id: UUID = Field(foreign_key="emails.id", index=True)

    # Event info
    event_type: EventType
    timestamp: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False, index=True),
    )

    # Details
    description: str | None = Field(default=None, max_length=500)
    metadata_json: str | None = Field(default=None)  # JSON for extra data

    # Provider webhook data
    provider_event_id: str | None = Field(default=None, max_length=255)
    raw_payload: str | None = Field(default=None)  # Raw webhook JSON

    # For tracking events
    ip_address: str | None = Field(default=None, max_length=45)
    user_agent: str | None = Field(default=None, max_length=500)
    link_url: str | None = Field(default=None)  # For click events

    # Relationship
    email: Email = Relationship(back_populates="events")


class EmailAttachment(SQLModel, table=True):
    """Email attachments metadata."""

    __tablename__ = "email_attachments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email_id: UUID = Field(foreign_key="emails.id", index=True)

    # File info
    filename: str = Field(max_length=255)
    content_type: str = Field(max_length=100)
    size_bytes: int

    # Storage
    storage_path: str | None = Field(default=None, max_length=500)  # S3/MinIO path
    content_base64: str | None = Field(default=None)  # For small files, inline

    # Disposition
    is_inline: bool = Field(default=False)  # Inline (cid:) vs attachment
    content_id: str | None = Field(default=None, max_length=100)  # For inline images

    # Timestamps
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )

    # Relationship
    email: Email = Relationship(back_populates="attachments")
