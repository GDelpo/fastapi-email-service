"""SMTP email provider implementation.

Full-featured SMTP sending with:
- Multiple recipients with names
- CC/BCC support
- Attachments (inline and regular)
- TLS/SSL support
- Authentication
"""

import base64
import uuid
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from aiosmtplib import SMTP

from app.models import Email, EmailAttachment
from app.providers.base import BaseEmailProvider, ProviderCapabilities, SendResult


class SMTPProvider(BaseEmailProvider):
    """Email provider using SMTP protocol.

    Features:
    - Direct SMTP connection
    - TLS/STARTTLS support
    - Multiple recipients with names
    - Attachments (inline and regular)
    """

    # SMTP capabilities
    capabilities = ProviderCapabilities(
        supports_attachments=True,
        supports_templates=False,  # No native template support
        supports_tracking=False,  # No open/click tracking
        supports_scheduling=False,  # No native scheduling
        max_recipients=100,
        max_attachment_size_mb=25.0,
        max_attachments=20,
    )

    def __init__(
        self,
        host: str,
        port: int = 25,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = False,
        sender_email: str | None = None,
        sender_name: str | None = None,
    ):
        """Initialize SMTP provider.

        Args:
            host: SMTP server hostname
            port: SMTP server port
            username: Optional authentication username
            password: Optional authentication password
            use_tls: Whether to use STARTTLS
            sender_email: Default sender email
            sender_name: Default sender name
        """
        super().__init__(provider_name="smtp")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.sender_email = sender_email
        self.sender_name = sender_name or "Mail Service"

        self.logger.debug(
            "SMTP provider configured",
            extra={
                "extra_fields": {
                    "host": host,
                    "port": port,
                    "use_tls": use_tls,
                    "has_auth": bool(username and password),
                }
            },
        )

    async def _send(self, email: Email) -> SendResult:
        """Send email via SMTP.

        Args:
            email: Email model to send

        Returns:
            SendResult: With generated message ID

        Raises:
            Exception: If SMTP send fails
        """
        # Generate message ID
        message_id = f"<{uuid.uuid4()}@{self.host}>"

        # Build MIME message
        has_attachments = bool(email.attachments)

        if has_attachments:
            message = MIMEMultipart("mixed")
            body_part = MIMEMultipart("alternative")
        else:
            message = MIMEMultipart("alternative")
            body_part = message

        # Headers
        message["Message-ID"] = message_id

        # Sender
        sender_email = email.from_email or self.sender_email
        sender_name = email.from_name or self.sender_name
        message["From"] = formataddr((sender_name, sender_email))

        # Recipients - To
        to_recipients = self._parse_recipients(email.to_emails)
        message["To"] = ", ".join(
            [formataddr((r.name or "", r.email)) for r in to_recipients]
        )

        # Subject
        message["Subject"] = email.subject

        # Collect TO emails for deduplication (case-insensitive)
        seen_emails = {r.email.lower() for r in to_recipients}

        # CC (skip duplicates already in TO)
        if email.cc_emails:
            cc_recipients = self._parse_recipients(email.cc_emails)
            cc_unique = [r for r in cc_recipients if r.email.lower() not in seen_emails]
            if cc_unique:
                message["Cc"] = ", ".join(
                    [formataddr((r.name or "", r.email)) for r in cc_unique]
                )
                for r in cc_unique:
                    seen_emails.add(r.email.lower())

        # Reply-To
        if email.reply_to:
            message["Reply-To"] = email.reply_to

        # Body - plain text
        if email.text_content:
            body_part.attach(MIMEText(email.text_content, "plain", "utf-8"))

        # Body - HTML
        if email.html_content:
            body_part.attach(MIMEText(email.html_content, "html", "utf-8"))

        # If we have attachments, attach the body part to the main message
        if has_attachments:
            message.attach(body_part)

            # Add attachments
            for att in email.attachments:
                self._add_attachment(message, att)

        # Get all recipients (including BCC)
        all_recipients = self._build_recipients_list(email)

        # Send via SMTP
        self.logger.debug(
            f"Connecting to SMTP server {self.host}:{self.port}",
            extra={
                "extra_fields": {
                    "recipients_count": len(all_recipients),
                    "has_attachments": has_attachments,
                    "attachment_count": len(email.attachments)
                    if email.attachments
                    else 0,
                }
            },
        )

        smtp_client = SMTP(
            hostname=self.host,
            port=self.port,
            start_tls=self.use_tls,
        )

        await smtp_client.connect()

        try:
            if self.username and self.password:
                await smtp_client.login(self.username, self.password)

            await smtp_client.send_message(message, recipients=all_recipients)
        finally:
            await smtp_client.quit()

        return SendResult(
            success=True,
            provider_message_id=message_id,
        )

    async def _test_connection(self) -> bool:
        """Test SMTP server connection.

        Returns:
            bool: True if connection works
        """
        smtp_client = SMTP(
            hostname=self.host,
            port=self.port,
            start_tls=self.use_tls,
        )

        await smtp_client.connect()

        try:
            if self.username and self.password:
                await smtp_client.login(self.username, self.password)
            return True
        finally:
            await smtp_client.quit()

    def _add_attachment(
        self, message: MIMEMultipart, attachment: EmailAttachment
    ) -> None:
        """Add attachment to MIME message.

        Args:
            message: MIME message to attach to
            attachment: EmailAttachment with base64 content
        """
        # Content in DB is already clean base64 (re-encoded by repository)
        content = base64.b64decode(attachment.content_base64)

        # Determine main type and subtype
        main_type, _, sub_type = attachment.content_type.partition("/")
        if not sub_type:
            main_type = "application"
            sub_type = "octet-stream"

        part = MIMEBase(main_type, sub_type)
        part.set_payload(content)

        # Encode as base64 for transport
        from email import encoders

        encoders.encode_base64(part)

        # Set disposition
        if attachment.is_inline:
            part.add_header(
                "Content-Disposition",
                "inline",
                filename=attachment.filename,
            )
            if attachment.content_id:
                part.add_header("Content-ID", f"<{attachment.content_id}>")
        else:
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=attachment.filename,
            )

        message.attach(part)

        self.logger.debug(
            "Attached file to SMTP message",
            extra={
                "extra_fields": {
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size_bytes": attachment.size_bytes,
                    "is_inline": attachment.is_inline,
                }
            },
        )
