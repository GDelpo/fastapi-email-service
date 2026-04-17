"""Webhook schemas."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class EmailProvider(str, Enum):
    """Available email providers."""

    SMTP = "smtp"
    SENDGRID = "sendgrid"
    MAILCHIMP = "mailchimp"


class WebhookEvent(BaseModel):
    """Incoming webhook event from provider."""

    provider: EmailProvider
    event_type: str
    email_id: str | None = None
    provider_message_id: str | None = None
    timestamp: datetime | None = None
    raw_data: dict[str, Any] = {}
