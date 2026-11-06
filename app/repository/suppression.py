"""Repository: Database operations for email suppression list."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.logger import get_logger
from app.models import EmailSuppression, SuppressionReason, utc_now

logger = get_logger(__name__)


class SuppressionRepository:
    """Database operations for email suppression list."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        email: str,
        reason: SuppressionReason,
        source: str,
        description: str | None = None,
        provider_message: str | None = None,
        related_email_id: UUID | None = None,
        expires_at: datetime | None = None,
        created_by: str | None = None,
    ) -> EmailSuppression:
        """Add email to suppression list.

        If email already exists, updates the reason and reactivates if needed.
        """
        # Normalize email to lowercase
        email_normalized = email.lower().strip()

        # Check if already exists
        stmt = select(EmailSuppression).where(EmailSuppression.email == email_normalized)
        result = await self.session.execute(stmt)
        existing = result.scalars().first()

        if existing:
            # Update existing suppression
            existing.reason = reason
            existing.source = source
            existing.description = description
            existing.provider_message = provider_message
            existing.related_email_id = related_email_id
            existing.expires_at = expires_at
            existing.is_active = True
            existing.updated_at = utc_now()

            logger.info(
                "Updated suppression",
                extra={"extra_fields": {"email": email_normalized, "reason": reason.value}},
            )

            await self.session.commit()
            await self.session.refresh(existing)
            return existing

        # Create new suppression
        suppression = EmailSuppression(
            email=email_normalized,
            reason=reason,
            source=source,
            description=description,
            provider_message=provider_message,
            related_email_id=related_email_id,
            expires_at=expires_at,
            created_by=created_by,
        )

        self.session.add(suppression)
        await self.session.commit()
        await self.session.refresh(suppression)

        logger.info(
            "Added email to suppression list",
            extra={
                "extra_fields": {
                    "email": email_normalized,
                    "reason": reason.value,
                    "source": source,
                }
            },
        )

        return suppression

    async def is_suppressed(self, email: str) -> EmailSuppression | None:
        """Check if email is in active suppression list.

        Returns the suppression record if found and active, None otherwise.
        Automatically handles expiration.
        """
        email_normalized = email.lower().strip()
        now = utc_now()

        stmt = select(EmailSuppression).where(
            EmailSuppression.email == email_normalized,
            EmailSuppression.is_active.is_(True),
            or_(
                EmailSuppression.expires_at.is_(None),
                EmailSuppression.expires_at > now,
            ),
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def check_multiple(self, emails: list[str]) -> dict[str, EmailSuppression]:
        """Check multiple emails for suppression.

        Returns dict of email -> suppression for suppressed emails only.
        """
        if not emails:
            return {}

        emails_normalized = [e.lower().strip() for e in emails]
        now = utc_now()

        stmt = select(EmailSuppression).where(
            EmailSuppression.email.in_(emails_normalized),
            EmailSuppression.is_active.is_(True),
            or_(
                EmailSuppression.expires_at.is_(None),
                EmailSuppression.expires_at > now,
            ),
        )
        result = await self.session.execute(stmt)
        suppressions = result.scalars().all()

        return {s.email: s for s in suppressions}

    async def remove(self, email: str) -> bool:
        """Remove email from suppression list (deactivate).

        Returns True if email was found and deactivated.
        """
        email_normalized = email.lower().strip()

        stmt = select(EmailSuppression).where(EmailSuppression.email == email_normalized)
        result = await self.session.execute(stmt)
        suppression = result.scalars().first()

        if not suppression:
            return False

        suppression.is_active = False
        suppression.updated_at = utc_now()
        await self.session.commit()

        logger.info(
            "Removed email from suppression list",
            extra={"extra_fields": {"email": email_normalized}},
        )

        return True

    async def count(
        self,
        active_only: bool = True,
        reason: SuppressionReason | None = None,
    ) -> int:
        """Count suppressions with optional filtering."""
        stmt = select(func.count(EmailSuppression.id))

        if active_only:
            stmt = stmt.where(EmailSuppression.is_active.is_(True))

        if reason:
            stmt = stmt.where(EmailSuppression.reason == reason)

        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def get_all(
        self,
        active_only: bool = True,
        reason: SuppressionReason | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EmailSuppression]:
        """Get suppressions with optional filtering."""
        stmt = select(EmailSuppression).order_by(EmailSuppression.created_at.desc())

        if active_only:
            stmt = stmt.where(EmailSuppression.is_active.is_(True))

        if reason:
            stmt = stmt.where(EmailSuppression.reason == reason)

        stmt = stmt.offset(offset).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_stats(self) -> dict:
        """Get suppression statistics."""
        # Total count
        total_stmt = select(func.count(EmailSuppression.id))
        total_result = await self.session.execute(total_stmt)
        total = total_result.scalar() or 0

        # Active count
        active_stmt = select(func.count(EmailSuppression.id)).where(
            EmailSuppression.is_active.is_(True)
        )
        active_result = await self.session.execute(active_stmt)
        active = active_result.scalar() or 0

        # By reason
        reason_stmt = (
            select(
                EmailSuppression.reason,
                func.count(EmailSuppression.id),
            )
            .where(EmailSuppression.is_active.is_(True))
            .group_by(EmailSuppression.reason)
        )

        reason_result = await self.session.execute(reason_stmt)
        by_reason = {str(r.value): c for r, c in reason_result.all()}

        return {
            "total": total,
            "active": active,
            "by_reason": by_reason,
        }

    async def cleanup_expired(self) -> int:
        """Deactivate expired suppressions.

        Returns count of deactivated entries.
        """
        now = utc_now()

        stmt = select(EmailSuppression).where(
            EmailSuppression.is_active.is_(True),
            EmailSuppression.expires_at.isnot(None),
            EmailSuppression.expires_at <= now,
        )
        result = await self.session.execute(stmt)
        expired = result.scalars().all()

        for suppression in expired:
            suppression.is_active = False
            suppression.updated_at = now

        await self.session.commit()

        if expired:
            logger.info(
                f"Cleaned up {len(expired)} expired suppressions",
                extra={"extra_fields": {"count": len(expired)}},
            )

        return len(expired)
