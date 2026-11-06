"""Repository: Database operations for Email entity."""

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.exceptions import DatabaseError, EntityNotFoundError
from app.logger import get_logger
from app.models import (
    BounceType,
    Email,
    EmailAttachment,
    EmailEvent,
    EmailPriority,
    EmailStatus,
    EmailType,
    EventType,
    utc_now,
)
from app.schemas import AttachmentCreate, EmailCreate, EmailRecipient
from app.utils import decode_base64_async

logger = get_logger(__name__)


def recipients_to_json(recipients: list[EmailRecipient] | None) -> str | None:
    """Convert recipient list to JSON string."""
    if not recipients:
        return None
    return json.dumps([r.model_dump() for r in recipients])


class EmailRepository:
    """Database operations for Email entity."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ========================================================================
    # Create
    # ========================================================================

    async def create(
        self,
        email_data: EmailCreate,
        owner_id: UUID | None = None,
        source_service: str | None = None,
        batch_id: UUID | None = None,
    ) -> Email:
        """Create new email record.

        Args:
            email_data: Email creation data
            owner_id: UUID of the owner/requester
            source_service: Name of the calling service
            batch_id: UUID of the batch (for bulk sends)

        Returns:
            Created Email model
        """
        # Convert recipients to JSON
        to_json = recipients_to_json(email_data.to)
        cc_json = recipients_to_json(email_data.cc) if email_data.cc else None
        bcc_json = recipients_to_json(email_data.bcc) if email_data.bcc else None

        email = Email(
            from_email=email_data.from_email or "",
            from_name=email_data.from_name,
            to_emails=to_json or "[]",
            cc_emails=cc_json,
            bcc_emails=bcc_json,
            reply_to=email_data.reply_to,
            subject=email_data.subject or "",
            html_content=email_data.html_content,
            text_content=email_data.text_content,
            status=EmailStatus.QUEUED,
            priority=email_data.priority,
            email_type=email_data.email_type,
            scheduled_at=email_data.scheduled_at,
            source_service=source_service,
            source_request_id=email_data.source_request_id,
            owner_id=owner_id,
            batch_id=batch_id,
        )

        try:
            self.session.add(email)
            await self.session.flush()  # Get the ID

            # Add attachments if any
            if email_data.attachments:
                for att_data in email_data.attachments:
                    await self._add_attachment(email.id, att_data)

            # Create initial event
            await self._add_event(
                email_id=email.id,
                event_type=EventType.CREATED,
                description="Email created",
            )

            await self.session.commit()

            # Reload with relations for proper serialization in async context
            return await self.get(email.id)

        except SQLAlchemyError as e:
            logger.error("Error creating email: %s", e)
            await self.session.rollback()
            raise DatabaseError("create", str(e)) from None

    async def _add_attachment(
        self,
        email_id: UUID,
        att_data: AttachmentCreate,
    ) -> EmailAttachment:
        """Add attachment to email and optionally save to disk."""
        # Decode base64 content (tolerant to PHP padding/whitespace quirks)
        content_bytes = await decode_base64_async(att_data.content_base64)
        size_bytes = len(content_bytes)

        # Re-encode as clean base64 so all downstream consumers (SendGrid,
        # SMTP, download endpoint) receive a well-formed string.
        clean_base64 = base64.b64encode(content_bytes).decode("ascii")

        storage_path = None

        # Save file to disk if enabled
        if settings.attachments_store_files:
            storage_path = self._save_attachment_file(
                email_id=email_id,
                filename=att_data.filename,
                content=content_bytes,
            )

        attachment = EmailAttachment(
            email_id=email_id,
            filename=att_data.filename,
            content_type=att_data.content_type,
            size_bytes=size_bytes,
            storage_path=storage_path,
            content_base64=clean_base64,
            is_inline=att_data.is_inline,
            content_id=att_data.content_id,
        )
        self.session.add(attachment)
        return attachment

    def _save_attachment_file(
        self,
        email_id: UUID,
        filename: str,
        content: bytes,
    ) -> str:
        """Save attachment file to disk.

        Args:
            email_id: Email UUID
            filename: Original filename
            content: File content bytes

        Returns:
            Relative path to saved file
        """
        # Create directory structure: attachments/{email_id}/
        base_path = Path(settings.attachments_storage_path)
        email_dir = base_path / str(email_id)
        email_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize filename (remove path separators)
        safe_filename = filename.replace("/", "_").replace("\\", "_")
        file_path = email_dir / safe_filename

        # Handle duplicate filenames
        counter = 1
        original_stem = file_path.stem
        original_suffix = file_path.suffix
        while file_path.exists():
            file_path = email_dir / f"{original_stem}_{counter}{original_suffix}"
            counter += 1

        # Write file
        file_path.write_bytes(content)

        logger.debug(
            "Attachment saved to disk",
            extra={
                "extra_fields": {
                    "email_id": str(email_id),
                    "filename": filename,
                    "path": str(file_path),
                    "size_bytes": len(content),
                }
            },
        )

        # Return relative path from storage root
        return str(file_path.relative_to(base_path))

    async def _add_event(
        self,
        email_id: UUID,
        event_type: EventType,
        description: str | None = None,
        metadata: dict | None = None,
        **kwargs,
    ) -> EmailEvent:
        """Add event to email timeline."""
        event = EmailEvent(
            email_id=email_id,
            event_type=event_type,
            description=description,
            metadata_json=json.dumps(metadata) if metadata else None,
            **kwargs,
        )
        self.session.add(event)
        return event

    # ========================================================================
    # Read
    # ========================================================================

    async def get(self, email_id: UUID, with_relations: bool = True) -> Email:
        """Get email by ID.

        Args:
            email_id: Email UUID
            with_relations: Include attachments and events (default: True for async safety)

        Returns:
            Email model

        Raises:
            EntityNotFoundError: If email not found
        """
        stmt = select(Email).where(Email.id == email_id)

        if with_relations:
            stmt = stmt.options(
                selectinload(Email.attachments),
                selectinload(Email.events),
            )

        result = await self.session.execute(stmt)
        email = result.scalars().first()

        if not email:
            raise EntityNotFoundError("Email", str(email_id))

        return email

    async def get_all(
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
        limit: int | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> list[Email]:
        """Get emails with optional filters.

        Args:
            status: Filter by status
            priority: Filter by priority
            email_type: Filter by type
            owner_id: Filter by owner
            source_service: Filter by source service
            search: Search by ID (exact) or subject (ILIKE)
            skip: Offset for pagination
            limit: Limit results
            sort_by: Column to sort by
            sort_order: asc or desc

        Returns:
            List of Email models
        """
        col = getattr(Email, sort_by, Email.created_at)
        order = col.asc() if sort_order == "asc" else col.desc()
        stmt = select(Email).order_by(order)

        if status:
            stmt = stmt.where(Email.status == status)
        if priority:
            stmt = stmt.where(Email.priority == priority)
        if email_type:
            stmt = stmt.where(Email.email_type == email_type)
        if owner_id:
            stmt = stmt.where(Email.owner_id == owner_id)
        if source_service:
            stmt = stmt.where(Email.source_service == source_service)
        if search:
            # Try UUID exact match, otherwise ILIKE on subject
            try:
                search_uuid = UUID(search)
                stmt = stmt.where(Email.id == search_uuid)
            except (ValueError, AttributeError):
                stmt = stmt.where(Email.subject.ilike(f"%{search}%"))
        if to_email:
            stmt = stmt.where(Email.to_emails.ilike(f"%{to_email}%"))
        if date_from:
            stmt = stmt.where(Email.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Email.created_at <= date_to)

        stmt = stmt.offset(skip)
        if limit:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_queued(
        self,
        limit: int = 100,
        priority: EmailPriority | None = None,
    ) -> list[Email]:
        """Get queued emails for processing, ordered by priority.

        Args:
            limit: Maximum emails to retrieve
            priority: Optional filter by priority

        Returns:
            List of queued Email models (critical first)
        """
        now = utc_now()

        stmt = (
            select(Email)
            .where(
                and_(
                    Email.status == EmailStatus.QUEUED,
                    or_(
                        Email.scheduled_at.is_(None),
                        Email.scheduled_at <= now,
                    ),
                )
            )
            .order_by(
                # Priority order: critical=0, high=1, normal=2, low=3
                Email.priority.asc(),
                Email.created_at.asc(),
            )
            .limit(limit)
        )

        if priority:
            stmt = stmt.where(Email.priority == priority)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_pending_retry(self, limit: int = 50) -> list[Email]:
        """Get failed emails ready for retry.

        Args:
            limit: Maximum emails to retrieve

        Returns:
            List of Email models ready for retry
        """
        now = utc_now()

        stmt = (
            select(Email)
            .where(
                and_(
                    Email.status == EmailStatus.FAILED,
                    Email.retry_count < Email.max_retries,
                    or_(
                        Email.next_retry_at.is_(None),
                        Email.next_retry_at <= now,
                    ),
                )
            )
            .order_by(Email.priority.asc(), Email.next_retry_at.asc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(
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
        stmt = select(func.count()).select_from(Email)

        if status:
            stmt = stmt.where(Email.status == status)
        if priority:
            stmt = stmt.where(Email.priority == priority)
        if email_type:
            stmt = stmt.where(Email.email_type == email_type)
        if owner_id:
            stmt = stmt.where(Email.owner_id == owner_id)
        if source_service:
            stmt = stmt.where(Email.source_service == source_service)
        if search:
            try:
                search_uuid = UUID(search)
                stmt = stmt.where(Email.id == search_uuid)
            except (ValueError, AttributeError):
                stmt = stmt.where(Email.subject.ilike(f"%{search}%"))
        if to_email:
            stmt = stmt.where(Email.to_emails.ilike(f"%{to_email}%"))
        if date_from:
            stmt = stmt.where(Email.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Email.created_at <= date_to)

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_events(self, email_id: UUID) -> list[EmailEvent]:
        """Get all events for an email."""
        stmt = (
            select(EmailEvent)
            .where(EmailEvent.email_id == email_id)
            .order_by(EmailEvent.timestamp.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_attachment(self, email_id: UUID, attachment_id: UUID) -> EmailAttachment:
        """Get a specific attachment by ID.

        Args:
            email_id: Email UUID (for validation)
            attachment_id: Attachment UUID

        Returns:
            EmailAttachment model

        Raises:
            EntityNotFoundError: If attachment not found or doesn't belong to email
        """
        stmt = select(EmailAttachment).where(
            and_(
                EmailAttachment.id == attachment_id,
                EmailAttachment.email_id == email_id,
            )
        )
        result = await self.session.execute(stmt)
        attachment = result.scalars().first()

        if not attachment:
            raise EntityNotFoundError("Attachment", str(attachment_id))

        return attachment

    def get_attachment_file_path(self, attachment: EmailAttachment) -> Path | None:
        """Get full file path for an attachment.

        Args:
            attachment: EmailAttachment model

        Returns:
            Full Path to file, or None if not stored on disk
        """
        if not attachment.storage_path:
            return None

        base_path = Path(settings.attachments_storage_path)
        return base_path / attachment.storage_path

    # ========================================================================
    # Update Status
    # ========================================================================

    async def queue(self, email_id: UUID) -> Email:
        """Queue email for sending."""
        email = await self.get(email_id, with_relations=False)
        email.status = EmailStatus.QUEUED
        email.updated_at = utc_now()

        await self._add_event(email_id, EventType.QUEUED, "Email queued for sending")
        await self.session.commit()

        logger.info("Email queued", extra={"extra_fields": {"email_id": str(email_id)}})
        return await self.get(email_id)

    async def mark_processing(self, email_id: UUID) -> Email:
        """Mark email as being processed."""
        email = await self.get(email_id, with_relations=False)
        email.status = EmailStatus.PROCESSING
        email.updated_at = utc_now()

        await self._add_event(email_id, EventType.PROCESSING, "Email processing started")
        await self.session.commit()
        return await self.get(email_id)

    async def mark_sent(
        self,
        email_id: UUID,
        provider: str,
        provider_message_id: str | None = None,
    ) -> Email:
        """Mark email as sent."""
        email = await self.get(email_id)
        email.status = EmailStatus.SENT
        email.provider = provider
        email.provider_message_id = provider_message_id
        email.sent_at = utc_now()
        email.updated_at = utc_now()

        await self._add_event(
            email_id,
            EventType.SENT,
            f"Email sent via {provider}",
            metadata={"provider_message_id": provider_message_id},
        )
        await self.session.commit()

        logger.info(
            "Email sent",
            extra={
                "extra_fields": {
                    "email_id": str(email_id),
                    "provider": provider,
                    "provider_message_id": provider_message_id,
                }
            },
        )
        return await self.get(email_id)

    async def mark_delivered(self, email_id: UUID) -> Email:
        """Mark email as delivered (from webhook)."""
        email = await self.get(email_id)
        email.status = EmailStatus.DELIVERED
        email.delivered_at = utc_now()
        email.updated_at = utc_now()

        await self._add_event(email_id, EventType.DELIVERED, "Email delivered")
        await self.session.commit()
        return await self.get(email_id)

    async def mark_failed(
        self,
        email_id: UUID,
        reason: str,
        schedule_retry: bool = True,
    ) -> Email:
        """Mark email as failed with optional retry scheduling."""
        email = await self.get(email_id)
        email.status = EmailStatus.FAILED
        email.last_error = reason[:500] if reason else "Unknown error"
        email.retry_count += 1
        email.updated_at = utc_now()

        # Schedule retry with exponential backoff
        if schedule_retry and email.retry_count < email.max_retries:
            backoff_minutes = 2**email.retry_count  # 2, 4, 8, 16...
            email.next_retry_at = utc_now() + timedelta(minutes=backoff_minutes)

            await self._add_event(
                email_id,
                EventType.RETRY_SCHEDULED,
                f"Retry #{email.retry_count} scheduled for {email.next_retry_at}",
                metadata={
                    "retry_count": email.retry_count,
                    "backoff_minutes": backoff_minutes,
                },
            )
        else:
            await self._add_event(
                email_id,
                EventType.FAILED,
                f"Email failed: {reason[:100]}",
                metadata={
                    "retry_count": email.retry_count,
                    "max_retries": email.max_retries,
                },
            )

        await self.session.commit()

        logger.warning(
            "Email failed",
            extra={
                "extra_fields": {
                    "email_id": str(email_id),
                    "reason": reason[:100],
                    "retry_count": email.retry_count,
                    "next_retry": str(email.next_retry_at) if email.next_retry_at else None,
                }
            },
        )
        return await self.get(email_id)

    async def mark_bounced(
        self,
        email_id: UUID,
        bounce_type: BounceType,
        reason: str | None = None,
    ) -> Email:
        """Mark email as bounced (from webhook)."""
        email = await self.get(email_id)
        email.status = EmailStatus.BOUNCED
        email.bounced_at = utc_now()
        email.bounce_type = bounce_type
        email.bounce_reason = reason
        email.updated_at = utc_now()

        event_type = (
            EventType.HARD_BOUNCE if bounce_type == BounceType.HARD else EventType.SOFT_BOUNCE
        )
        await self._add_event(email_id, event_type, f"Bounce: {reason}")
        await self.session.commit()
        return await self.get(email_id)

    async def mark_opened(
        self,
        email_id: UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Email:
        """Mark email as opened (from tracking pixel)."""
        email = await self.get(email_id)
        if not email.opened_at:  # Only update on first open
            email.status = EmailStatus.OPENED
            email.opened_at = utc_now()
            email.updated_at = utc_now()

        await self._add_event(
            email_id,
            EventType.OPENED,
            "Email opened",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self.session.commit()
        return await self.get(email_id)

    async def mark_clicked(
        self,
        email_id: UUID,
        link_url: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Email:
        """Mark email link as clicked."""
        email = await self.get(email_id)
        if not email.clicked_at:
            email.status = EmailStatus.CLICKED
            email.clicked_at = utc_now()
            email.updated_at = utc_now()

        await self._add_event(
            email_id,
            EventType.CLICKED,
            f"Link clicked: {link_url[:100]}",
            link_url=link_url,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self.session.commit()
        return await self.get(email_id)

    async def cancel(self, email_id: UUID, reason: str | None = None) -> Email:
        """Cancel a queued email."""
        email = await self.get(email_id)

        if email.status not in [EmailStatus.DRAFT, EmailStatus.QUEUED]:
            raise DatabaseError("cancel", f"Cannot cancel email with status {email.status}")

        email.status = EmailStatus.CANCELLED
        email.updated_at = utc_now()

        await self._add_event(email_id, EventType.CANCELLED, reason or "Cancelled by user")
        await self.session.commit()

        logger.info("Email cancelled", extra={"extra_fields": {"email_id": str(email_id)}})
        return await self.get(email_id)

    async def reset_for_retry(self, email_id: UUID) -> Email:
        """Reset failed email for immediate retry."""
        email = await self.get(email_id)
        email.status = EmailStatus.QUEUED
        email.retry_count = 0
        email.next_retry_at = None
        email.last_error = None
        email.updated_at = utc_now()

        await self._add_event(email_id, EventType.QUEUED, "Email reset for retry")
        await self.session.commit()
        return await self.get(email_id)

    # ========================================================================
    # Delete
    # ========================================================================

    async def delete(self, email_id: UUID) -> None:
        """Delete email by ID."""
        email = await self.get(email_id, with_relations=True)

        try:
            await self.session.delete(email)
            await self.session.commit()
            logger.info("Email deleted", extra={"extra_fields": {"email_id": str(email_id)}})

        except SQLAlchemyError as e:
            logger.error("Error deleting email %s: %s", email_id, e)
            await self.session.rollback()
            raise DatabaseError("delete", str(e)) from None
