"""
ARQ Worker Configuration.

Run with: arq app.worker.WorkerSettings

This is the entry point for the background worker process.
"""


from arq import cron
from arq.connections import RedisSettings

from app.config import settings
from app.logger import configure_logging, get_logger
from app.tasks import (
    cleanup_old_events_task,
    health_check_task,
    process_retry_batch_task,
    process_scheduled_task,
    send_email_task,
)

# Configure logging
configure_logging()
logger = get_logger(__name__)


async def startup(ctx: dict) -> None:
    """Called when worker starts up."""
    logger.info(
        "🚀 ARQ Worker starting",
        extra={
            "extra_fields": {
                "environment": settings.environment,
                "redis_host": settings.redis_host,
            }
        },
    )

    # Initialize any shared resources
    # The email provider is created per-task to ensure fresh connections


async def shutdown(ctx: dict) -> None:
    """Called when worker shuts down."""
    logger.info("👋 ARQ Worker shutting down")


async def on_job_start(ctx: dict) -> None:
    """Called before each job starts."""
    job_id = ctx.get("job_id", "unknown")
    logger.debug("Starting job %s", job_id)


async def on_job_end(ctx: dict) -> None:
    """Called after each job completes."""
    job_id = ctx.get("job_id", "unknown")
    logger.debug("Completed job %s", job_id)


class WorkerSettings:
    """ARQ Worker settings."""

    # Redis connection
    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_db,
        password=settings.redis_password.get_secret_value()
        if settings.redis_password
        else None,
    )

    # Job functions available to the worker
    functions = [
        send_email_task,
        process_retry_batch_task,
        process_scheduled_task,
        cleanup_old_events_task,
        health_check_task,
    ]

    # Scheduled/cron jobs
    cron_jobs = [
        # Process retries every 2 minutes
        cron(
            process_retry_batch_task,
            minute={
                0,
                2,
                4,
                6,
                8,
                10,
                12,
                14,
                16,
                18,
                20,
                22,
                24,
                26,
                28,
                30,
                32,
                34,
                36,
                38,
                40,
                42,
                44,
                46,
                48,
                50,
                52,
                54,
                56,
                58,
            },
            run_at_startup=True,
        ),
        # Process scheduled emails every minute
        cron(
            process_scheduled_task,
            minute=set(range(60)),  # Every minute
            run_at_startup=True,
        ),
        # Health check every 5 minutes
        cron(
            health_check_task,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
        ),
        # Cleanup old events weekly (Sunday at 3 AM)
        cron(
            cleanup_old_events_task,
            weekday={6},  # Sunday
            hour={3},
            minute={0},
        ),
    ]

    # Lifecycle hooks
    on_startup = startup
    on_shutdown = shutdown
    on_job_start = on_job_start
    on_job_end = on_job_end

    # Worker configuration
    max_jobs = 10  # Concurrent jobs
    job_timeout = 300  # 5 minutes per job
    keep_result = 3600  # Keep results for 1 hour
    max_tries = 3  # Retry failed jobs up to 3 times

    # Health check
    health_check_interval = 30  # seconds

    # Queue name - use default ARQ queue
    # NOTE: Must match the queue used in app/queue.py enqueue_job calls
    queue_name = "arq:queue"
