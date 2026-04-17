"""Health check schemas."""

from enum import Enum

from app.schemas.common import CamelModel


class ServiceStatus(str, Enum):
    """Service health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class DependencyStatus(CamelModel):
    """Status of a single dependency."""

    status: ServiceStatus
    latency_ms: float | None = None
    error: str | None = None


class HealthResponse(CamelModel):
    """Health check response."""

    status: ServiceStatus = ServiceStatus.HEALTHY
    version: str
    environment: str
    dependencies: dict[str, DependencyStatus] = {}
