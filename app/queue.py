"""
ARQ Queue Client for enqueuing jobs from the API.

This module provides a simple interface to enqueue email jobs
to be processed by the ARQ worker.
"""

from typing import Any
from uuid import UUID

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)

# Singleton pool instance
_pool: ArqRedis | None = None


def get_redis_settings() -> RedisSettings:
    """Get Redis settings for ARQ."""
    return RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_db,
        password=settings.redis_password.get_secret_value() if settings.redis_password else None,
    )


async def get_queue_pool() -> ArqRedis:
    """Get or create ARQ connection pool.

    Returns:
        ArqRedis connection pool
    """
    global _pool
    if _pool is None:
        _pool = await create_pool(get_redis_settings())
        logger.info("✅ ARQ queue pool initialized")
    return _pool


async def close_queue_pool() -> None:
    """Close the ARQ connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("ARQ queue pool closed")


# ============================================================================
# Job Enqueueing Functions
# ============================================================================


async def enqueue_send_email(email_id: UUID, defer_seconds: int = 0) -> str | None:
    """Enqueue an email for sending.

    Args:
        email_id: UUID of the email to send
        defer_seconds: Delay before processing (for scheduling)

    Returns:
        Job ID if enqueued successfully, None otherwise
    """
    try:
        pool = await get_queue_pool()

        if defer_seconds > 0:
            from datetime import timedelta

            job = await pool.enqueue_job(
                "send_email_task",
                str(email_id),
                _defer_by=timedelta(seconds=defer_seconds),
            )
        else:
            job = await pool.enqueue_job("send_email_task", str(email_id))

        if job:
            logger.info(
                "📬 Email enqueued",
                extra={
                    "extra_fields": {
                        "email_id": str(email_id),
                        "job_id": job.job_id,
                        "defer_seconds": defer_seconds,
                    }
                },
            )
            return job.job_id
        return None

    except Exception as e:
        logger.error(
            "Failed to enqueue email",
            extra={"extra_fields": {"email_id": str(email_id), "error": str(e)}},
        )
        return None


async def enqueue_batch_send(email_ids: list[UUID]) -> int:
    """Enqueue multiple emails for sending.

    Args:
        email_ids: List of email UUIDs

    Returns:
        Number of successfully enqueued jobs
    """
    pool = await get_queue_pool()
    enqueued = 0

    for email_id in email_ids:
        try:
            job = await pool.enqueue_job("send_email_task", str(email_id))
            if job:
                enqueued += 1
        except Exception as e:
            logger.error("Failed to enqueue email %s: %s", email_id, e)

    logger.info(
        "📬 Batch enqueued",
        extra={"extra_fields": {"total": len(email_ids), "enqueued": enqueued}},
    )

    return enqueued


async def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Get status of a queued job.

    Args:
        job_id: ARQ job ID

    Returns:
        Job status dict or None if not found
    """
    try:
        pool = await get_queue_pool()
        job = await pool.job(job_id)

        if job is None:
            return None

        status = await job.status()
        result = await job.result(poll_delay=0)

        return {
            "job_id": job_id,
            "status": status.name if status else "unknown",
            "result": result,
        }

    except Exception as e:
        logger.error("Failed to get job status: %s", e)
        return None


async def get_queue_info() -> dict[str, Any]:
    """Get queue information.

    Returns:
        Dict with queue stats
    """
    try:
        pool = await get_queue_pool()
        info = await pool.info()

        return {
            "queued_jobs": info.get("queued", 0) if info else 0,
            "complete_jobs": info.get("complete", 0) if info else 0,
            "in_progress": info.get("in_progress", 0) if info else 0,
        }

    except Exception as e:
        logger.error("Failed to get queue info: %s", e)
        return {"error": str(e)}
