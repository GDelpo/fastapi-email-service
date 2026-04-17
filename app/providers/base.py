"""Base email provider abstract class.

All email providers must inherit from this class.
"""

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.exceptions import (
    AttachmentError,
    EmailProviderError,
    EmailSendError,
)
from app.logger import get_logger
from app.models import Email
from app.schemas import EmailRecipient


@dataclass
class ProviderCapabilities:
    """Provider capabilities and limits."""

    supports_attachments: bool = True
    supports_templates: bool = False
    supports_tracking: bool = False
    supports_scheduling: bool = False
    max_recipients: int = 50
    max_attachment_size_mb: float = 25.0
    max_attachments: int = 10


@dataclass
class SendResult:
    """Result of sending an email."""

    success: bool
    provider_message_id: str | None = None
    error: str | None = None
    raw_response: dict[str, Any] | None = None


class BaseEmailProvider(ABC):
    """Abstract base class for email providers.

    Provides common functionality:
    - Connection testing with retries
    - Performance logging
    - Error handling patterns
    - Capability validation
    - Standard interface for all providers

    Subclasses must implement:
    - _send(): Provider-specific sending logic
    - _test_connection(): Provider-specific connection test
    """

    # Default capabilities - override in subclass
    capabilities = ProviderCapabilities()

    def __init__(self, provider_name: str):
        """Initialize base provider.

        Args:
            provider_name: Name for logging (e.g., "smtp", "sendgrid")
        """
        self.provider_name = provider_name
        self.logger = get_logger(f"provider.{provider_name}")
        self._is_available: bool | None = None
        self._last_error: str | None = None

    @property
    def is_available(self) -> bool:
        """Check if provider is available."""
        return self._is_available is True

    def get_status(self) -> tuple[bool, str | None]:
        """Get provider status and last error."""
        return self._is_available or False, self._last_error

    def get_capabilities(self) -> ProviderCapabilities:
        """Get provider capabilities."""
        return self.capabilities

    def validate_email(self, email: Email) -> None:
        """Validate email against provider capabilities.

        Args:
            email: Email to validate

        Raises:
            AttachmentError: If attachments not supported or exceed limits
            EmailProviderError: If other validation fails
        """
        # Check attachments support
        if email.attachments and not self.capabilities.supports_attachments:
            raise AttachmentError(
                f"Provider '{self.provider_name}' does not support attachments. "
                f"Please use a different provider (smtp or sendgrid)."
            )

        # Check attachment count
        if (
            email.attachments
            and len(email.attachments) > self.capabilities.max_attachments
        ):
            raise AttachmentError(
                f"Too many attachments. Maximum: {self.capabilities.max_attachments}, "
                f"Provided: {len(email.attachments)}"
            )

        # Check attachment sizes
        if email.attachments:
            max_size_bytes = self.capabilities.max_attachment_size_mb * 1024 * 1024
            for att in email.attachments:
                if att.size_bytes > max_size_bytes:
                    raise AttachmentError(
                        f"Attachment '{att.filename}' exceeds maximum size. "
                        f"Maximum: {self.capabilities.max_attachment_size_mb}MB"
                    )

        # Check recipient count
        recipients = self._parse_recipients(email.to_emails)
        if email.cc_emails:
            recipients.extend(self._parse_recipients(email.cc_emails))
        if email.bcc_emails:
            recipients.extend(self._parse_recipients(email.bcc_emails))

        if len(recipients) > self.capabilities.max_recipients:
            raise EmailProviderError(
                f"Too many recipients. Maximum: {self.capabilities.max_recipients}, "
                f"Provided: {len(recipients)}"
            )

    async def send(self, email: Email) -> SendResult:
        """Send email with logging, validation, and error handling.

        Args:
            email: Email model to send

        Returns:
            SendResult: Result with provider message ID

        Raises:
            EmailSendError: If sending fails
            AttachmentError: If attachment validation fails
        """
        start_time = time.time()
        recipients = self._parse_recipients(email.to_emails)
        recipient_str = ", ".join([r.email for r in recipients[:3]])
        if len(recipients) > 3:
            recipient_str += f" (+{len(recipients) - 3} more)"

        self.logger.info(
            f"📧 Sending email via {self.provider_name.upper()}",
            extra={
                "extra_fields": {
                    "provider": self.provider_name,
                    "recipients": recipient_str,
                    "subject": email.subject[:50],
                    "email_id": str(email.id),
                    "priority": email.priority.value,
                    "has_attachments": bool(email.attachments),
                }
            },
        )

        try:
            # Validate before sending
            self.validate_email(email)

            # Provider-specific send
            result = await self._send(email)

            duration_ms = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ Email sent successfully via {self.provider_name.upper()}",
                extra={
                    "extra_fields": {
                        "provider": self.provider_name,
                        "recipients": recipient_str,
                        "email_id": str(email.id),
                        "provider_message_id": result.provider_message_id,
                        "duration_ms": round(duration_ms, 2),
                    }
                },
            )
            return result

        except (AttachmentError, EmailProviderError):
            # Re-raise validation errors as-is
            raise

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ Failed to send email via {self.provider_name.upper()}",
                extra={
                    "extra_fields": {
                        "provider": self.provider_name,
                        "recipients": recipient_str,
                        "email_id": str(email.id),
                        "error": str(e),
                        "duration_ms": round(duration_ms, 2),
                    }
                },
                exc_info=True,
            )
            raise EmailSendError(recipient_str, str(e)) from e

    async def test_connection(self) -> bool:
        """Test connection to email provider.

        Returns:
            bool: True if connection successful
        """
        try:
            self.logger.debug("Testing %s connection...", self.provider_name.upper())
            result = await self._test_connection()

            if result:
                self._is_available = True
                self._last_error = None
                self.logger.info(
                    f"✅ {self.provider_name.upper()} connection verified",
                    extra={"extra_fields": {"provider": self.provider_name}},
                )
            else:
                self._is_available = False
                self._last_error = "Connection test returned False"

            return result

        except Exception as e:
            self._is_available = False
            self._last_error = str(e)
            self.logger.error(
                f"❌ {self.provider_name.upper()} connection failed",
                extra={
                    "extra_fields": {
                        "provider": self.provider_name,
                        "error": str(e),
                    }
                },
            )
            return False

    @abstractmethod
    async def _send(self, email: Email) -> SendResult:
        """Provider-specific sending logic.

        Must be implemented by each provider.

        Args:
            email: Email model to send

        Returns:
            SendResult: With provider message ID

        Raises:
            Exception: On send failure
        """
        pass

    @abstractmethod
    async def _test_connection(self) -> bool:
        """Provider-specific connection test.

        Must be implemented by each provider.

        Returns:
            bool: True if connection works

        Raises:
            Exception: On connection failure
        """
        pass

    def _parse_recipients(self, recipients_json: str | None) -> list[EmailRecipient]:
        """Parse recipients from JSON string.

        Args:
            recipients_json: JSON string of recipients

        Returns:
            List of EmailRecipient
        """
        if not recipients_json:
            return []

        try:
            data = json.loads(recipients_json)
            return [
                EmailRecipient(**r) if isinstance(r, dict) else EmailRecipient(email=r)
                for r in data
            ]
        except (json.JSONDecodeError, TypeError):
            # Fallback: try comma-separated
            return [
                EmailRecipient(email=e.strip())
                for e in recipients_json.split(",")
                if e.strip()
            ]

    def _build_recipients_list(self, email: Email) -> list[str]:
        """Build flat list of unique recipient emails.

        Args:
            email: Email model

        Returns:
            List of unique recipient email addresses (deduplicated, case-insensitive)
        """
        recipients = []
        seen = set()

        for r in self._parse_recipients(email.to_emails):
            key = r.email.lower()
            if key not in seen:
                recipients.append(r.email)
                seen.add(key)

        for r in self._parse_recipients(email.cc_emails):
            key = r.email.lower()
            if key not in seen:
                recipients.append(r.email)
                seen.add(key)

        for r in self._parse_recipients(email.bcc_emails):
            key = r.email.lower()
            if key not in seen:
                recipients.append(r.email)
                seen.add(key)

        return recipients
