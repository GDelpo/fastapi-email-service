"""Repository: Database operations for email batches (bulk sends)."""

import json
from uuid import UUID

from sqlalchemy import and_, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.exceptions import EntityNotFoundError
from app.logger import get_logger
from app.models import (
    BatchStatus,
    Email,
    EmailBatch,
    EmailPriority,
    EmailType,
    utc_now,
)

logger = get_logger(__name__)


class BatchRepository:
    """Database operations for email batches (bulk sends)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        from_email: str,
        from_name: str | None = None,
        reply_to: str | None = None,
        template_id: UUID | None = None,
        subject_template: str | None = None,
        html_template: str | None = None,
        text_template: str | None = None,
        name: str | None = None,
        priority: EmailPriority = EmailPriority.LOW,
        email_type: EmailType = EmailType.MARKETING,
        skip_suppressed: bool = True,
        total_recipients: int = 0,
        owner_id: UUID | None = None,
        source_service: str | None = None,
    ) -> EmailBatch:
        """Create a new email batch."""
        batch = EmailBatch(
            name=name,
            status=BatchStatus.PENDING,
            template_id=template_id,
            subject_template=subject_template,
            html_template=html_template,
            text_template=text_template,
            from_email=from_email,
            from_name=from_name,
            reply_to=reply_to,
            priority=priority,
            email_type=email_type,
            skip_suppressed=skip_suppressed,
            total_recipients=total_recipients,
            owner_id=owner_id,
            source_service=source_service,
        )

        self.session.add(batch)
        await self.session.commit()
        await self.session.refresh(batch)

        logger.info("Created batch %s with %s recipients", batch.id, total_recipients)
        return batch

    async def get(self, batch_id: UUID) -> EmailBatch:
        """Get batch by ID."""
        result = await self.session.get(EmailBatch, batch_id)
        if not result:
            raise EntityNotFoundError("EmailBatch", str(batch_id))
        return result

    async def update_status(
        self,
        batch_id: UUID,
        status: BatchStatus,
        queued_count: int | None = None,
        skipped_count: int | None = None,
        skipped_details: list[dict] | None = None,
    ) -> EmailBatch:
        """Update batch status and counts."""
        batch = await self.get(batch_id)
        batch.status = status
        batch.updated_at = utc_now()

        if queued_count is not None:
            batch.queued_count = queued_count
        if skipped_count is not None:
            batch.skipped_count = skipped_count
        if skipped_details is not None:
            batch.skipped_details = json.dumps(skipped_details)

        if status == BatchStatus.COMPLETED:
            batch.completed_at = utc_now()

        await self.session.commit()
        await self.session.refresh(batch)
        return batch

    async def increment_sent(self, batch_id: UUID) -> None:
        """Increment sent count for batch."""
        batch = await self.get(batch_id)
        batch.sent_count += 1
        batch.updated_at = utc_now()
        await self.session.commit()

    async def increment_failed(self, batch_id: UUID) -> None:
        """Increment failed count for batch."""
        batch = await self.get(batch_id)
        batch.failed_count += 1
        batch.updated_at = utc_now()
        await self.session.commit()

    async def get_batch_emails(
        self,
        batch_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Email], int]:
        """Get emails belonging to a batch."""
        # Count
        count_stmt = select(func.count(Email.id)).where(Email.batch_id == batch_id)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Get emails
        stmt = (
            select(Email)
            .where(Email.batch_id == batch_id)
            .order_by(Email.created_at)
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        emails = result.scalars().all()

        return list(emails), total

    async def list_batches(
        self,
        skip: int = 0,
        limit: int = 20,
        status: BatchStatus | None = None,
        source_service: str | None = None,
    ) -> tuple[list[EmailBatch], int]:
        """List batches with filters."""
        # Build query
        conditions = []
        if status:
            conditions.append(EmailBatch.status == status)
        if source_service:
            conditions.append(EmailBatch.source_service == source_service)

        # Count
        count_stmt = select(func.count(EmailBatch.id))
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Get batches
        stmt = select(EmailBatch).order_by(EmailBatch.created_at.desc())
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.offset(skip).limit(limit)

        result = await self.session.execute(stmt)
        batches = result.scalars().all()

        return list(batches), total
