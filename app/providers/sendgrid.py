"""SendGrid email provider implementation.

Based on official SendGrid Python SDK documentation:
- Single/multiple recipients
- Attachments with base64 encoding
- CC/BCC support
"""

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Attachment,
    Bcc,
    Cc,
    ContentId,
    Disposition,
    FileContent,
    FileName,
    FileType,
    Mail,
    To,
)

from app.models import Email, EmailAttachment
from app.providers.base import BaseEmailProvider, ProviderCapabilities, SendResult


class SendGridProvider(BaseEmailProvider):
    """Email provider using SendGrid API.

    Features:
    - Multiple recipients with names
    - Attachments (inline and regular)
    - Tracking (opens, clicks)
    - Templates (via SendGrid)
    """

    # SendGrid capabilities
    capabilities = ProviderCapabilities(
        supports_attachments=True,
        supports_templates=True,  # SendGrid has dynamic templates
        supports_tracking=True,  # Open and click tracking
        supports_scheduling=True,  # Can schedule sends
        max_recipients=1000,
        max_attachment_size_mb=30.0,
        max_attachments=10,
    )

    def __init__(
        self,
        api_key: str,
        from_email: str,
        from_name: str = "Mail Service",
        verify_ssl: bool = True,
    ):
        """Initialize SendGrid provider.

        Args:
            api_key: SendGrid API key
            from_email: Verified sender email address
            from_name: Sender display name
            verify_ssl: Whether to verify SSL certificates
        """
        super().__init__(provider_name="sendgrid")
        self.api_key = api_key
        self.from_email = from_email
        self.from_name = from_name
        self.verify_ssl = verify_ssl

        # Configure SSL if needed
        if not verify_ssl:
            import ssl

            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            ssl._create_default_https_context = ssl._create_unverified_context
            self.logger.warning("⚠️ SSL verification disabled (not recommended for production)")

        self.client = SendGridAPIClient(api_key=api_key)
        self.logger.debug(
            "SendGrid provider configured",
            extra={"extra_fields": {"from_email": from_email}},
        )

    async def _send(self, email: Email) -> SendResult:
        """Send email via SendGrid API.

        Args:
            email: Email model to send

        Returns:
            SendResult: With provider message ID

        Raises:
            Exception: If SendGrid API fails
        """
        # Parse recipients
        to_recipients = self._parse_recipients(email.to_emails)

        # Build To list with names
        to_emails = [
            To(email=r.email, name=r.name) if r.name else To(email=r.email) for r in to_recipients
        ]

        # Determine sender
        sender_email = email.from_email or self.from_email
        sender_name = email.from_name or self.from_name

        # SendGrid requires at least one content block
        # Use html_content if available, otherwise use text_content
        primary_content = email.html_content or email.text_content
        if not primary_content:
            raise Exception("Email must have either html_content or text_content")

        message = Mail(
            from_email=(sender_email, sender_name),
            to_emails=to_emails,
            subject=email.subject,
            html_content=email.html_content if email.html_content else None,
            plain_text_content=email.text_content if not email.html_content else None,
        )

        # Add plain text as secondary content if we have both
        if email.html_content and email.text_content:
            message.add_content(email.text_content, "text/plain")

        # Collect all TO emails (lowercased) for deduplication
        to_email_set = {r.email.lower() for r in to_recipients}

        # Add CC recipients (skip duplicates already in TO)
        if email.cc_emails:
            cc_recipients = self._parse_recipients(email.cc_emails)
            for r in cc_recipients:
                if r.email.lower() not in to_email_set:
                    message.add_cc(Cc(email=r.email, name=r.name) if r.name else Cc(email=r.email))
                    to_email_set.add(r.email.lower())

        # Add BCC recipients (skip duplicates already in TO or CC)
        if email.bcc_emails:
            bcc_recipients = self._parse_recipients(email.bcc_emails)
            for r in bcc_recipients:
                if r.email.lower() not in to_email_set:
                    message.add_bcc(
                        Bcc(email=r.email, name=r.name) if r.name else Bcc(email=r.email)
                    )
                    to_email_set.add(r.email.lower())

        # Add reply-to
        if email.reply_to:
            message.reply_to = email.reply_to

        # Handle attachments
        if email.attachments:
            for att in email.attachments:
                self._add_attachment(message, att)

        # Send (run in executor to avoid blocking the event loop)
        # The SendGrid SDK is synchronous, so we wrap it
        import asyncio

        from python_http_client.exceptions import HTTPError

        # Compatible with both Python 3.12 (worker) and 3.14 (api)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            response = await loop.run_in_executor(None, self.client.send, message)
        except HTTPError as e:
            # Log detailed SendGrid error response
            error_body = e.body if hasattr(e, "body") else "No body"
            self.logger.error(
                f"SendGrid API error: {e}",
                extra={
                    "extra_fields": {
                        "status_code": getattr(e, "status_code", None),
                        "error_body": error_body,
                        "email_id": str(email.id),
                    }
                },
            )
            raise Exception(f"SendGrid error: {error_body}") from e

        if response.status_code not in [200, 201, 202]:
            raise Exception(f"SendGrid returned status {response.status_code}: {response.body}")

        # Extract message ID from headers
        message_id = None
        if response.headers:
            message_id = response.headers.get("X-Message-Id")

        self.logger.debug(
            "SendGrid response",
            extra={
                "extra_fields": {
                    "status_code": response.status_code,
                    "message_id": message_id,
                }
            },
        )

        return SendResult(
            success=True,
            provider_message_id=message_id,
            raw_response={"status_code": response.status_code},
        )

    async def _test_connection(self) -> bool:
        """Verify SendGrid API connection.

        Returns:
            bool: True if client is properly initialized
        """
        # SendGrid doesn't have a dedicated health endpoint
        # We verify the client is initialized correctly
        if not self.client or not self.api_key:
            raise Exception("SendGrid client not properly initialized")

        self.logger.debug("SendGrid client initialized and ready")
        return True

    def _add_attachment(self, message: Mail, attachment: EmailAttachment) -> None:
        """Add attachment to SendGrid message.

        Args:
            message: SendGrid Mail object
            attachment: EmailAttachment model with base64 content
        """
        sg_attachment = Attachment()
        sg_attachment.file_content = FileContent(attachment.content_base64)
        sg_attachment.file_type = FileType(attachment.content_type)
        sg_attachment.file_name = FileName(attachment.filename)

        # Inline (cid:) or attachment
        if attachment.is_inline:
            sg_attachment.disposition = Disposition("inline")
            if attachment.content_id:
                sg_attachment.content_id = ContentId(attachment.content_id)
        else:
            sg_attachment.disposition = Disposition("attachment")

        message.add_attachment(sg_attachment)

        self.logger.debug(
            "Attached file to SendGrid message",
            extra={
                "extra_fields": {
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size_bytes": attachment.size_bytes,
                    "is_inline": attachment.is_inline,
                }
            },
        )
