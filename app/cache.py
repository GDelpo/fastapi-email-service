"""Redis cache manager for Mail Service."""

import hashlib
import json
from typing import Any

from redis.asyncio import ConnectionPool, Redis

from app.logger import get_logger

logger = get_logger(__name__)


class CacheManager:
    """Redis cache manager with async support."""

    def __init__(self, redis_url: str):
        self.pool = ConnectionPool.from_url(redis_url, decode_responses=True)
        self.redis: Redis | None = None

    async def connect(self) -> None:
        """Connect to Redis."""
        try:
            self.redis = Redis(connection_pool=self.pool)
            await self.redis.ping()
            logger.info("✅ Redis connected")
        except Exception as e:
            logger.error("❌ Redis connection failed: %s", e)
            self.redis = None

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        if self.redis:
            await self.redis.aclose()
            await self.pool.aclose()

    async def ping(self) -> bool:
        """Check if Redis is available."""
        if not self.redis:
            return False
        try:
            await self.redis.ping()
            return True
        except Exception:
            return False

    # ========================================================================
    # Basic Operations
    # ========================================================================

    async def get(self, key: str) -> Any | None:
        """Get value from cache."""
        if not self.redis:
            return None
        try:
            value = await self.redis.get(key)
            return json.loads(value) if value else None
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Set value in cache with TTL (default 5 min)."""
        if not self.redis:
            return False
        try:
            await self.redis.setex(key, ttl, json.dumps(value, default=str))
            return True
        except Exception:
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self.redis:
            return False
        try:
            await self.redis.delete(key)
            return True
        except Exception:
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not self.redis:
            return False
        try:
            return await self.redis.exists(key) > 0
        except Exception:
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern."""
        if not self.redis:
            return 0
        try:
            keys = [k async for k in self.redis.scan_iter(match=pattern)]
            if keys:
                return await self.redis.delete(*keys)
            return 0
        except Exception:
            return 0

    # ========================================================================
    # Email-Specific Operations
    # ========================================================================

    async def check_duplicate(
        self,
        recipient: str,
        subject: str,
        body_hash: str,
        window_seconds: int = 300,
    ) -> bool:
        """Check if this email was recently sent (deduplication).

        Args:
            recipient: Email recipient
            subject: Email subject
            body_hash: Hash of email body
            window_seconds: Time window to check for duplicates

        Returns:
            True if duplicate found, False otherwise
        """
        key = cache_key(recipient, subject, body_hash, prefix="email:dedup")

        if await self.exists(key):
            logger.warning(
                "Duplicate email detected",
                extra={
                    "extra_fields": {
                        "recipient": recipient,
                        "subject": subject[:50],
                    }
                },
            )
            return True

        # Mark as sent
        await self.set(key, {"sent": True}, ttl=window_seconds)
        return False

    async def check_rate_limit(
        self,
        identifier: str,
        max_requests: int = 100,
        window_seconds: int = 3600,
    ) -> tuple[bool, int]:
        """Check rate limit for identifier.

        Args:
            identifier: User/service identifier
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds

        Returns:
            Tuple of (is_allowed, remaining_requests)
        """
        if not self.redis:
            return True, max_requests  # Allow if cache unavailable

        key = cache_key(identifier, prefix="email:ratelimit")

        try:
            current = await self.redis.get(key)
            count = int(current) if current else 0

            if count >= max_requests:
                logger.warning(
                    "Rate limit exceeded",
                    extra={
                        "extra_fields": {
                            "identifier": identifier,
                            "count": count,
                            "limit": max_requests,
                        }
                    },
                )
                return False, 0

            # Increment counter
            pipe = self.redis.pipeline()
            pipe.incr(key)
            if count == 0:
                pipe.expire(key, window_seconds)
            await pipe.execute()

            return True, max_requests - count - 1

        except Exception as e:
            logger.error("Rate limit check failed: %s", e)
            return True, max_requests  # Allow on error

    async def cache_provider_status(
        self,
        provider_name: str,
        is_available: bool,
        error: str | None = None,
        ttl: int = 60,
    ) -> None:
        """Cache provider status to avoid frequent connection tests.

        Args:
            provider_name: Name of the provider
            is_available: Whether provider is available
            error: Error message if unavailable
            ttl: Cache TTL in seconds
        """
        key = cache_key(provider_name, prefix="provider:status")
        await self.set(
            key,
            {"available": is_available, "error": error},
            ttl=ttl,
        )

    async def get_provider_status(self, provider_name: str) -> dict | None:
        """Get cached provider status.

        Args:
            provider_name: Name of the provider

        Returns:
            Cached status dict or None if not cached
        """
        key = cache_key(provider_name, prefix="provider:status")
        return await self.get(key)

    async def increment_counter(
        self,
        counter_name: str,
        ttl: int = 86400,  # 24 hours
    ) -> int:
        """Increment a counter (for metrics).

        Args:
            counter_name: Name of the counter
            ttl: Counter TTL

        Returns:
            New counter value
        """
        if not self.redis:
            return 0

        key = cache_key(counter_name, prefix="email:counter")

        try:
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl)
            results = await pipe.execute()
            return results[0]
        except Exception:
            return 0

    async def get_counter(self, counter_name: str) -> int:
        """Get counter value.

        Args:
            counter_name: Name of the counter

        Returns:
            Counter value or 0
        """
        if not self.redis:
            return 0

        key = cache_key(counter_name, prefix="email:counter")

        try:
            value = await self.redis.get(key)
            return int(value) if value else 0
        except Exception:
            return 0

    async def get_stats(self) -> dict:
        """Get email sending statistics.

        Returns:
            Dict with various counters
        """
        return {
            "emails_sent_today": await self.get_counter("sent:today"),
            "emails_failed_today": await self.get_counter("failed:today"),
            "rate_limited_today": await self.get_counter("ratelimited:today"),
            "duplicates_blocked_today": await self.get_counter("duplicates:today"),
        }


def cache_key(*args, prefix: str = "mail") -> str:
    """Generate consistent cache key.

    Args:
        *args: Key components
        prefix: Key prefix

    Returns:
        Formatted cache key
    """
    key_data = ":".join(str(arg) for arg in args if arg)
    key_hash = hashlib.md5(key_data.encode()).hexdigest()[:8]
    return f"{prefix}:{key_data}:{key_hash}"


def hash_email_body(body: str) -> str:
    """Generate hash of email body for deduplication.

    Args:
        body: Email HTML body

    Returns:
        MD5 hash of body
    """
    return hashlib.md5(body.encode()).hexdigest()
