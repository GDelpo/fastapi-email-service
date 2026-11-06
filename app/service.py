"""Service: Business logic and orchestration for emails."""

import hashlib
from datetime import datetime
from uuid import UUID

from jinja2 import Template

from app.cache import CacheManager
from app.config import settings
from app.exceptions import (
    AttachmentError,
    DuplicateEmailError,
    EmailError,
    EmailSendError,
    RateLimitError,
    SuppressionError,
    ValidationError,
)
from app.logger import get_logger
from app.models import BatchStatus, Email, EmailPriority, EmailStatus, EmailType
from app.providers.base import BaseEmailProvider
from app.queue import enqueue_send_email, get_queue_info
from app.repository import (
    BatchRepository,
    EmailRepository,
    SuppressionRepository,
    TemplateRepository,
)
from app.schemas import (
    BulkEmailCreate,
    BulkEmailResponse,
    EmailCreate,
    EmailRecipient,
    QueuedRecipient,
    QueueStats,
    SkippedRecipient,
)

logger = get_logger(__name__)


class EmailService:
    """Email domain service - orchestrates repository, provider, cache, and queue."""

    def __init__(
        self,
        repo: EmailRepository,
        provider: BaseEmailProvider,
        template_repo: TemplateRepository | None = None,
        cache: CacheManager | None = None,
        suppression_repo: SuppressionRepository | None = None,
        batch_repo: BatchRepository | None = None,
    ):
        self.repo = repo
        self.provider = provider
        self.template_repo = template_repo
        self.cache = cache
        self.suppression_repo = suppression_repo
        self.batch_repo = batch_repo

    # ========================================================================
    # Rate Limiting, Deduplication & Suppression
    # ========================================================================

    async def _check_rate_limit(self, identifier: str) -> None:
        """Check rate limit for the requester.

        Args:
            identifier: User ID or service name

        Raises:
            RateLimitError: If rate limit exceeded
        """
        if not self.cache or not settings.rate_limit_enabled:
            return

        allowed, remaining = await self.cache.check_rate_limit(
            identifier=identifier,
            max_requests=settings.rate_limit_max_emails,
            window_seconds=settings.rate_limit_window_seconds,
        )

        if not allowed:
            raise RateLimitError(
                f"Rate limit exceeded. Maximum {settings.rate_limit_max_emails} "
                f"emails per hour. Please try again later."
            )

        logger.debug(
            "Rate limit check passed",
            extra={"extra_fields": {"identifier": identifier, "remaining": remaining}},
        )

    async def _check_suppression(self, email_data: EmailCreate) -> None:
        """Check if any recipients are in the suppression list.

        Args:
            email_data: Email data with recipients

        Raises:
            SuppressionError: If any recipients are suppressed
        """
        if not self.suppression_repo:
            return

        # Collect all recipient emails
        all_emails = [r.email for r in email_data.to]
        if email_data.cc:
            all_emails.extend(r.email for r in email_data.cc)
        if email_data.bcc:
            all_emails.extend(r.email for r in email_data.bcc)

        if not all_emails:
            return

        # Check all at once
        suppressed = await self.suppression_repo.check_multiple(all_emails)

        if suppressed:
            suppressed_info = [
                {
                    "email": email,
                    "reason": supp.reason.value,
                    "description": supp.description,
                    "suppressed_at": supp.created_at.isoformat(),
                }
                for email, supp in suppressed.items()
            ]

            logger.warning(
                "Blocked email to suppressed recipients",
                extra={"extra_fields": {"suppressed": list(suppressed.keys())}},
            )

            raise SuppressionError(
                message=f"Cannot send to {len(suppressed)} suppressed email(s)",
                suppressed_emails=suppressed_info,
            )

    async def _check_duplicate(self, email_data: EmailCreate) -> None:
        """Check for duplicate email.

        Args:
            email_data: Email data to check

        Raises:
            DuplicateEmailError: If duplicate detected
        """
        if not self.cache or not settings.dedup_enabled:
            return

        # Create hash of email content
        content = (
            f"{email_data.subject}|{email_data.html_content or ''}|{email_data.text_content or ''}"
        )
        body_hash = hashlib.md5(content.encode()).hexdigest()

        # Check for first recipient only (to avoid marking all as dupes)
        if email_data.to:
            recipient = email_data.to[0]
            is_duplicate = await self.cache.check_duplicate(
                recipient=recipient.email,
                subject=email_data.subject or "",
                body_hash=body_hash,
                window_seconds=settings.dedup_window_seconds,
            )

            if is_duplicate:
                raise DuplicateEmailError(
                    f"Duplicate email detected for {recipient.email}. "
                    f"Same email sent within the last {settings.dedup_window_seconds} seconds."
                )

    # ========================================================================
    # Email Creation
    # ========================================================================

    async def create_email(
        self,
        email_data: EmailCreate,
        owner_id: UUID | None = None,
        source_service: str | None = None,
        sync: bool = False,
    ) -> Email:
        """Create and queue email for sending.

        Args:
            email_data: Email creation data
            owner_id: UUID of the requester
            source_service: Name of calling service
            sync: If True, send immediately (synchronous). If False, queue for background processing.

        Returns:
            Created Email model (queued or sent depending on sync flag)

        Raises:
            RateLimitError: If rate limit exceeded
            DuplicateEmailError: If duplicate detected
            AttachmentError: If attachments not supported
            SuppressionError: If recipient is in suppression list
        """
        # Check rate limit
        identifier = str(owner_id) if owner_id else source_service or "anonymous"
        await self._check_rate_limit(identifier)

        # Check if any recipients are in suppression list
        await self._check_suppression(email_data)

        # Validate attachments against provider
        if email_data.attachments and not self.provider.capabilities.supports_attachments:
            raise AttachmentError(
                f"Provider '{self.provider.provider_name}' does not support attachments. "
                f"Remove attachments or use a different provider (smtp, sendgrid)."
            )

        # If using template, resolve content
        if email_data.template_slug:
            email_data = await self._resolve_template(email_data)

        # Check for duplicates (after template resolution so we check actual content)
        await self._check_duplicate(email_data)

        # Set default sender if not provided (uses provider-specific from_email)
        if not email_data.from_email:
            email_data.from_email = settings.default_from_email

        # Create email (status = QUEUED by default)
        email = await self.repo.create(
            email_data=email_data,
            owner_id=owner_id,
            source_service=source_service,
        )

        logger.info(
            "📧 Email created",
            extra={
                "extra_fields": {
                    "email_id": str(email.id),
                    "priority": email.priority.value,
                    "source": source_service,
                    "sync": sync,
                }
            },
        )

        if sync:
            # Send immediately (synchronous)
            return await self.send_email(email.id)
        else:
            # Enqueue to ARQ worker for background processing
            job_id = await enqueue_send_email(email.id)
            if job_id:
                logger.info(
                    "📬 Email queued to worker",
                    extra={"extra_fields": {"email_id": str(email.id), "job_id": job_id}},
                )
            else:
                logger.warning(
                    "⚠️ Failed to enqueue to worker, will be picked up by cron",
                    extra={"extra_fields": {"email_id": str(email.id)}},
                )
            return email

    # ========================================================================
    # Bulk Email Creation
    # ========================================================================

    async def create_bulk_email(
        self,
        bulk_data: BulkEmailCreate,
        owner_id: UUID | None = None,
        source_service: str | None = None,
    ) -> BulkEmailResponse:
        """Create and queue bulk emails for sending.

        This method:
        1. Creates an EmailBatch record
        2. Checks all recipients against suppression list
        3. Resolves template if provided
        4. Creates individual Email records for each valid recipient
        5. Queues each email for background processing

        Args:
            bulk_data: Bulk email creation data
            owner_id: UUID of the requester
            source_service: Name of calling service

        Returns:
            BulkEmailResponse with batch info and skipped details
        """
        if not self.batch_repo:
            raise EmailError("Batch repository not configured")

        # Check rate limit for bulk (counts as 1 request for rate limiting)
        identifier = str(owner_id) if owner_id else source_service or "anonymous"
        await self._check_rate_limit(identifier)

        # Set default sender (uses provider-specific from_email)
        from_email = (
            str(bulk_data.from_email) if bulk_data.from_email else settings.default_from_email
        )
        # INTENTIONAL: without a sender every recipient would fail one by one inside
        # a batch that already exists; refuse the whole request up front instead.
        if not from_email:
            raise ValidationError(
                "No sender address: pass from_email or configure the provider's sender "
                "(MAIL_SENDER_EMAIL, SENDGRID_FROM_EMAIL or MAILCHIMP_FROM_EMAIL)"
            )

        # Resolve template content if using template_id
        subject_template: str | None = bulk_data.subject
        html_template: str | None = bulk_data.html_content
        text_template: str | None = bulk_data.text_content

        if bulk_data.template_id:
            if not self.template_repo:
                raise EmailError("Template repository not configured")

            template = await self.template_repo.get(bulk_data.template_id)
            subject_template = template.subject_template
            html_template = template.html_template
            text_template = template.text_template

        # Collect all recipient emails for suppression check
        all_emails = [r.email.lower() for r in bulk_data.recipients]

        # Check suppression list in batch
        suppressed_map: dict = {}
        if self.suppression_repo:
            suppressed_map = await self.suppression_repo.check_multiple(all_emails)

        # Build list of valid recipients and skipped
        skipped_details: list[SkippedRecipient] = []
        valid_recipients = []

        for recipient in bulk_data.recipients:
            email_lower = recipient.email.lower()

            if email_lower in suppressed_map:
                supp = suppressed_map[email_lower]
                if bulk_data.skip_suppressed:
                    skipped_details.append(
                        SkippedRecipient(
                            email=recipient.email,
                            reason="suppressed",
                            details=f"{supp.reason.value}: {supp.description or 'No details'}",
                            external_id=recipient.external_id,
                        )
                    )
                    continue
                else:
                    # Fail entire batch if skip_suppressed=False
                    raise SuppressionError(
                        message=f"Recipient {recipient.email} is suppressed",
                        suppressed_emails=[
                            {
                                "email": recipient.email,
                                "reason": supp.reason.value,
                                "description": supp.description,
                            }
                        ],
                    )

            valid_recipients.append(recipient)

        # Create batch record
        batch = await self.batch_repo.create(
            name=bulk_data.name,
            from_email=from_email,
            from_name=bulk_data.from_name,
            reply_to=str(bulk_data.reply_to) if bulk_data.reply_to else None,
            template_id=bulk_data.template_id,
            subject_template=subject_template,
            html_template=html_template,
            text_template=text_template,
            priority=bulk_data.priority,
            email_type=bulk_data.email_type,
            skip_suppressed=bulk_data.skip_suppressed,
            total_recipients=len(bulk_data.recipients),
            owner_id=owner_id,
            source_service=source_service,
        )

        # Create individual emails for each valid recipient
        queued_count = 0
        queued_details: list[QueuedRecipient] = []

        for recipient in valid_recipients:
            try:
                # Render templates with recipient variables
                variables = recipient.variables or {}
                variables["email"] = recipient.email
                variables["name"] = recipient.name or recipient.email.split("@")[0]

                rendered_subject = (
                    recipient.subject
                    if recipient.subject is not None
                    else self._render_template(subject_template or "", variables)
                )
                rendered_html = (
                    recipient.html_content
                    if recipient.html_content is not None
                    else self._render_template(html_template or "", variables)
                )
                rendered_text = (
                    self._render_template(text_template or "", variables) if text_template else None
                )

                # Create email data
                email_data = EmailCreate(
                    to=[EmailRecipient(email=recipient.email, name=recipient.name)],
                    from_email=from_email,
                    from_name=bulk_data.from_name,
                    subject=rendered_subject,
                    html_content=rendered_html,
                    text_content=rendered_text,
                    priority=bulk_data.priority,
                    email_type=bulk_data.email_type,
                )

                # Create email record (linked to batch)
                email = await self.repo.create(
                    email_data=email_data,
                    owner_id=owner_id,
                    source_service=source_service,
                    batch_id=batch.id,
                )

                # Enqueue for background processing
                await enqueue_send_email(email.id)
                queued_count += 1
                queued_details.append(
                    QueuedRecipient(
                        email=recipient.email,
                        message_id=str(email.id),
                        external_id=recipient.external_id,
                    )
                )

            except Exception as e:
                logger.error(
                    f"Failed to create email for {recipient.email}: {e}",
                    extra={
                        "extra_fields": {
                            "batch_id": str(batch.id),
                            "email": recipient.email,
                        }
                    },
                )
                skipped_details.append(
                    SkippedRecipient(
                        email=recipient.email,
                        reason="creation_error",
                        details=str(e),
                        external_id=recipient.external_id,
                    )
                )

        # Update batch with final counts
        final_status = BatchStatus.QUEUED if queued_count > 0 else BatchStatus.FAILED
        if skipped_details and queued_count > 0:
            final_status = BatchStatus.PARTIAL

        await self.batch_repo.update_status(
            batch_id=batch.id,
            status=final_status,
            queued_count=queued_count,
            skipped_count=len(skipped_details),
            skipped_details=[s.model_dump() for s in skipped_details],
        )

        logger.info(
            "📨 Bulk email batch created",
            extra={
                "extra_fields": {
                    "batch_id": str(batch.id),
                    "total": len(bulk_data.recipients),
                    "queued": queued_count,
                    "skipped": len(skipped_details),
                }
            },
        )

        return BulkEmailResponse(
            batch_id=batch.id,
            status=final_status.value,
            name=bulk_data.name,
            total_recipients=len(bulk_data.recipients),
            queued=queued_count,
            skipped=len(skipped_details),
            skipped_details=skipped_details,
            queued_details=queued_details,
            created_at=batch.created_at,
        )

    def _render_template(self, template_str: str, variables: dict) -> str:
        """Render a template string with Jinja2 variables.

        Supports both {{variable}} and {{ variable }} syntax.
        """
        if not template_str:
            return template_str

        try:
            template = Template(template_str)
            return template.render(**variables)
        except Exception as e:
            logger.warning("Template rendering failed: %s", e)
            return template_str

    # ========================================================================
    # Email Sending
    # ========================================================================

    async def send_email(self, email_id: UUID) -> Email:
        """Send a specific email by ID.

        Args:
            email_id: Email UUID

        Returns:
            Updated Email model
        """
        email = await self.repo.get(email_id, with_relations=True)

        # Mark as processing
        await self.repo.mark_processing(email_id)

        try:
            # Validate and send
            result = await self.provider.send(email)

            # Mark as sent
            email = await self.repo.mark_sent(
                email_id=email_id,
                provider=self.provider.provider_name,
                provider_message_id=result.provider_message_id,
            )

            return email

        except AttachmentError:
            # Don't retry attachment errors
            await self.repo.mark_failed(email_id, str(AttachmentError), schedule_retry=False)
            raise

        except EmailSendError as e:
            await self.repo.mark_failed(email_id, str(e))
            raise

        except Exception as e:
            await self.repo.mark_failed(email_id, str(e))
            raise EmailSendError(str(email_id), str(e)) from e

    async def retry_email(self, email_id: UUID, reset_count: bool = False) -> Email:
        """Retry a failed email by queuing it to the worker.

        Args:
            email_id: Email UUID
            reset_count: Reset retry counter

        Returns:
            Updated Email model
        """
        email = await self.repo.get(email_id, with_relations=True)

        if email.status not in [EmailStatus.FAILED, EmailStatus.BOUNCED]:
            raise EmailError(f"Cannot retry email with status {email.status}")

        if reset_count:
            await self.repo.reset_for_retry(email_id)
        else:
            await self.repo.queue(email_id)

        # Enqueue to worker (not direct send)
        await enqueue_send_email(email_id)

        return await self.repo.get(email_id, with_relations=True)

    async def cancel_email(self, email_id: UUID, reason: str | None = None) -> Email:
        """Cancel a queued email.

        Args:
            email_id: Email UUID
            reason: Cancellation reason

        Returns:
            Cancelled Email model
        """
        return await self.repo.cancel(email_id, reason)

    # ========================================================================
    # Email Retrieval
    # ========================================================================

    async def get_email(self, email_id: UUID, with_details: bool = False) -> Email:
        """Get email by ID.

        Args:
            email_id: Email UUID
            with_details: Include events and attachments (always loads for async safety)

        Returns:
            Email model
        """
        # Always load relations to avoid SQLAlchemy async lazy loading errors
        # The with_details param is kept for API compatibility but relations are always loaded
        return await self.repo.get(email_id, with_relations=True)

    async def list_emails(
        self,
        status: EmailStatus | None = None,
        priority: EmailPriority | None = None,
        email_type: EmailType | None = None,
        owner_id: UUID | None = None,
        source_service: str | None = None,
        search: str | None = None,
        to_email: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        skip: int = 0,
        limit: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> list[Email]:
        """List emails with optional filters."""
        return await self.repo.get_all(
            status=status,
            priority=priority,
            email_type=email_type,
            owner_id=owner_id,
            source_service=source_service,
            search=search,
            to_email=to_email,
            date_from=date_from,
            date_to=date_to,
            skip=skip,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def count_emails(
        self,
        status: EmailStatus | None = None,
        priority: EmailPriority | None = None,
        email_type: EmailType | None = None,
        owner_id: UUID | None = None,
        source_service: str | None = None,
        search: str | None = None,
        to_email: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        """Count emails matching filters."""
        return await self.repo.count(
            status=status,
            priority=priority,
            email_type=email_type,
            owner_id=owner_id,
            source_service=source_service,
            search=search,
            to_email=to_email,
            date_from=date_from,
            date_to=date_to,
        )

    async def get_events(self, email_id: UUID) -> list:
        """Get event timeline for an email."""
        return await self.repo.get_events(email_id)

    async def get_attachment(self, email_id: UUID, attachment_id: UUID) -> tuple:
        """Get attachment metadata and file path.

        Args:
            email_id: Email UUID
            attachment_id: Attachment UUID

        Returns:
            Tuple of (EmailAttachment, Path | None)
        """
        attachment = await self.repo.get_attachment(email_id, attachment_id)
        file_path = self.repo.get_attachment_file_path(attachment)
        return attachment, file_path

    async def get_queue_stats(self) -> QueueStats:
        """Get queue statistics from DB and ARQ."""
        # DB stats
        queued = await self.repo.count(status=EmailStatus.QUEUED)
        processing = await self.repo.count(status=EmailStatus.PROCESSING)
        pending_retry = len(await self.repo.get_pending_retry(limit=1000))

        # These are approximations (would need date filter for accuracy)
        failed_today = await self.repo.count(status=EmailStatus.FAILED)
        sent_today = await self.repo.count(status=EmailStatus.SENT)

        # Try to get ARQ queue info
        try:
            arq_info = await get_queue_info()
            logger.debug(
                "ARQ queue info",
                extra={"extra_fields": arq_info},
            )
        except Exception as e:
            logger.warning("Could not get ARQ queue info: %s", e)

        return QueueStats(
            queued=queued,
            processing=processing,
            pending_retry=pending_retry,
            failed_today=failed_today,
            sent_today=sent_today,
        )

    # ========================================================================
    # Template Support
    # ========================================================================

    async def _resolve_template(self, email_data: EmailCreate) -> EmailCreate:
        """Resolve template and variables into email content.

        Args:
            email_data: Email data with template_slug

        Returns:
            EmailCreate with resolved content
        """
        if not self.template_repo:
            raise EmailError("Template repository not configured")

        if not email_data.template_slug:
            return email_data

        template = await self.template_repo.get_by_slug(email_data.template_slug)
        variables = email_data.template_variables or {}

        # Render templates using the helper method
        subject = self._render_template(template.subject_template, variables)
        html_content = self._render_template(template.html_template, variables)
        text_content = (
            self._render_template(template.text_template, variables)
            if template.text_template
            else None
        )

        # Create new EmailCreate with resolved content
        return EmailCreate(
            to=email_data.to,
            cc=email_data.cc,
            bcc=email_data.bcc,
            reply_to=email_data.reply_to,
            from_email=email_data.from_email,
            from_name=email_data.from_name,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            attachments=email_data.attachments,
            priority=email_data.priority,
            email_type=template.email_type,
            scheduled_at=email_data.scheduled_at,
            source_service=email_data.source_service,
            source_request_id=email_data.source_request_id,
        )

    # ========================================================================
    # Provider Info
    # ========================================================================

    def get_provider_status(self) -> dict:
        """Get current email provider status."""
        available, error = self.provider.get_status()
        caps = self.provider.get_capabilities()

        return {
            "name": self.provider.provider_name,
            "available": available,
            "error": error,
            "capabilities": {
                "supports_attachments": caps.supports_attachments,
                "supports_templates": caps.supports_templates,
                "supports_tracking": caps.supports_tracking,
                "supports_scheduling": caps.supports_scheduling,
                "max_recipients": caps.max_recipients,
                "max_attachment_size_mb": caps.max_attachment_size_mb,
            },
        }
