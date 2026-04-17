"""
Background tasks definitions for ARQ worker.

These are the actual job functions that run in the worker process.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

from app.database import async_session_maker
from app.logger import configure_logging, get_logger
from app.models import Email, EmailStatus
from app.providers.factory import create_email_provider
from app.repository import EmailRepository

# Configure logging for worker
configure_logging()
logger = get_logger(__name__)


# ============================================================================
# Email Sending Tasks
# ============================================================================


async def send_email_task(ctx: dict, email_id: str) -> dict[str, Any]:
    """Send a single email.

    This task is enqueued when an email is created with queue=True.

    Args:
        ctx: ARQ context (contains redis connection)
        email_id: UUID of the email to send

    Returns:
        Result dict with status and details
    """
    email_uuid = UUID(email_id)

    logger.info(
        "📧 Processing email task",
        extra={"extra_fields": {"email_id": email_id, "job_id": ctx.get("job_id")}},
    )

    async with async_session_maker() as session:
        repo = EmailRepository(session)
        provider = create_email_provider()

        try:
            # Get email
            email = await repo.get(email_uuid, with_relations=True)

            # Check if still queued (might have been cancelled)
            if email.status not in [EmailStatus.QUEUED, EmailStatus.FAILED]:
                logger.info(
                    f"Email {email_id} status is {email.status}, skipping",
                    extra={
                        "extra_fields": {
                            "email_id": email_id,
                            "status": str(email.status),
                        }
                    },
                )
                return {
                    "status": "skipped",
                    "reason": f"Email status is {email.status}",
                }

            # Mark as processing
            await repo.mark_processing(email_uuid)

            # Send via provider
            result = await provider.send(email)

            if result.success:
                # Mark as sent
                await repo.mark_sent(
                    email_id=email_uuid,
                    provider=provider.provider_name,
                    provider_message_id=result.provider_message_id,
                )

                logger.info(
                    "✅ Email sent successfully",
                    extra={
                        "extra_fields": {
                            "email_id": email_id,
                            "provider": provider.provider_name,
                            "provider_message_id": result.provider_message_id,
                        }
                    },
                )

                return {
                    "status": "sent",
                    "provider": provider.provider_name,
                    "provider_message_id": result.provider_message_id,
                }
            else:
                # Mark as failed (will schedule retry)
                await repo.mark_failed(email_uuid, result.error or "Unknown error")

                return {"status": "failed", "error": result.error}

        except Exception as e:
            logger.error(
                "❌ Email send failed",
                extra={"extra_fields": {"email_id": email_id, "error": str(e)}},
                exc_info=True,
            )

            # Mark as failed
            try:
                await repo.mark_failed(email_uuid, str(e))
            except Exception:
                pass

            # Re-raise for ARQ retry mechanism
            raise


async def process_retry_batch_task(ctx: dict, batch_size: int = 50) -> dict[str, Any]:
    """Process emails that are ready for retry.

    This is a scheduled task that runs periodically.

    Args:
        ctx: ARQ context
        batch_size: Maximum emails to process

    Returns:
        Result dict with counts
    """
    logger.info("🔄 Processing retry batch (size=%s)", batch_size)

    async with async_session_maker() as session:
        repo = EmailRepository(session)

        # Get emails ready for retry
        pending = await repo.get_pending_retry(limit=batch_size)

        if not pending:
            logger.debug("No emails pending retry")
            return {"processed": 0, "sent": 0, "failed": 0}

        logger.info("Found %s emails for retry", len(pending))

        # Enqueue each for individual processing
        redis = ctx.get("redis")
        if redis:
            from arq import create_pool

            from app.worker import WorkerSettings

            pool = await create_pool(WorkerSettings.redis_settings)
            for email in pending:
                await pool.enqueue_job("send_email_task", str(email.id))
            await pool.close()

        return {"enqueued": len(pending)}


async def process_scheduled_task(ctx: dict, batch_size: int = 50) -> dict[str, Any]:
    """Process scheduled emails that are due.

    This is a scheduled task that runs periodically.

    Args:
        ctx: ARQ context
        batch_size: Maximum emails to process

    Returns:
        Result dict with counts
    """
    from sqlalchemy import and_
    from sqlmodel import select

    from app.models import utc_now

    logger.info("⏰ Processing scheduled emails (size=%s)", batch_size)

    async with async_session_maker() as session:
        now = utc_now()

        # Find queued emails with scheduled_at <= now
        stmt = (
            select(Email)
            .where(
                and_(
                    Email.status == EmailStatus.QUEUED,
                    Email.scheduled_at.isnot(None),
                    Email.scheduled_at <= now,
                )
            )
            .order_by(Email.priority.asc(), Email.scheduled_at.asc())
            .limit(batch_size)
        )

        result = await session.execute(stmt)
        scheduled = list(result.scalars().all())

        if not scheduled:
            logger.debug("No scheduled emails due")
            return {"processed": 0}

        logger.info("Found %s scheduled emails due", len(scheduled))

        # Enqueue each for processing
        redis = ctx.get("redis")
        if redis:
            from arq import create_pool

            from app.worker import WorkerSettings

            pool = await create_pool(WorkerSettings.redis_settings)
            for email in scheduled:
                await pool.enqueue_job("send_email_task", str(email.id))
            await pool.close()

        return {"enqueued": len(scheduled)}


async def cleanup_old_events_task(ctx: dict, days: int = 90) -> dict[str, Any]:
    """Clean up old email events to prevent table bloat.

    Runs weekly to remove events older than X days.

    Args:
        ctx: ARQ context
        days: Keep events from last N days

    Returns:
        Result dict with deleted count
    """

    from sqlalchemy import delete

    from app.models import EmailEvent, utc_now

    logger.info("🧹 Cleaning up events older than %s days", days)

    cutoff = utc_now() - timedelta(days=days)

    async with async_session_maker() as session:
        stmt = delete(EmailEvent).where(EmailEvent.timestamp < cutoff)
        result = await session.execute(stmt)
        await session.commit()

        deleted = result.rowcount
        logger.info("Deleted %s old events", deleted)

        return {"deleted": deleted}


# ============================================================================
# Health Check Task
# ============================================================================


async def health_check_task(ctx: dict) -> dict[str, Any]:
    """Periodic health check for monitoring.

    Logs worker status and queue depth.
    """
    from app.models import EmailStatus

    async with async_session_maker() as session:
        repo = EmailRepository(session)

        queued = await repo.count(status=EmailStatus.QUEUED)
        processing = await repo.count(status=EmailStatus.PROCESSING)
        failed = await repo.count(status=EmailStatus.FAILED)

        logger.info(
            "💓 Worker health check",
            extra={
                "extra_fields": {
                    "queued": queued,
                    "processing": processing,
                    "failed": failed,
                }
            },
        )

        return {
            "status": "healthy",
            "queued": queued,
            "processing": processing,
            "failed": failed,
        }
