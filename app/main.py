"""Email Service - Main application entry point.

This application is infrastructure-agnostic. It runs the same way:
- Locally on port 8081
- Behind Traefik with any path prefix
- Behind Nginx or any other reverse proxy

The proxy is responsible for:
- Path prefix stripping (via StripPrefix middleware)
- Setting X-Forwarded-* headers
- TLS termination

FastAPI automatically handles X-Forwarded-* headers for proper URL generation.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from app.api import router as emails_router
from app.cache import CacheManager
from app.config import settings
from app.dashboard.routes import router as dashboard_router
from app.database import async_session_maker
from app.exceptions import (
    ServiceException,
    general_exception_handler,
    service_exception_handler,
    sqlalchemy_exception_handler,
    validation_exception_handler,
)
from app.logger import configure_logging, get_logger
from app.middleware import ProxyHeadersMiddleware, RequestLoggingMiddleware
from app.providers.factory import create_email_provider, get_available_providers
from app.schemas import DependencyStatus, HealthResponse, ServiceStatus
from app.suppression_api import router as suppressions_router
from app.webhooks import router as webhooks_router

# Configure logging before anything else
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan: startup and shutdown events."""
    logger.info(
        "🚀 Starting Email Service",
        extra={
            "extra_fields": {
                "version": settings.project_version,
                "environment": settings.environment,
                "debug": settings.debug,
                "log_level": settings.log_level,
                "email_provider": settings.email_provider,
            }
        },
    )

    # Initialize Redis cache
    try:
        app_instance.state.cache_manager = CacheManager(settings.redis_url)
        await app_instance.state.cache_manager.connect()
        logger.info(
            "✅ Redis cache initialized",
            extra={"extra_fields": {"service": "redis"}},
        )
    except Exception as e:
        logger.warning(
            "⚠️ Redis unavailable - running without cache",
            extra={"extra_fields": {"service": "redis", "error": str(e)}},
        )
        app_instance.state.cache_manager = CacheManager(settings.redis_url)

    # Initialize email provider
    try:
        provider = create_email_provider()
        app_instance.state.email_provider = provider

        # Test connection
        provider_available = await provider.test_connection()
        if provider_available:
            logger.info(
                f"✅ Email provider initialized: {settings.email_provider.upper()}",
                extra={"extra_fields": {"provider": settings.email_provider}},
            )
        else:
            logger.warning(
                "⚠️ Email provider unavailable - running in degraded mode",
                extra={"extra_fields": {"provider": settings.email_provider}},
            )
    except Exception as e:
        logger.error(
            "❌ Failed to initialize email provider",
            extra={
                "extra_fields": {
                    "provider": settings.email_provider,
                    "error": str(e),
                }
            },
            exc_info=True,
        )
        # Create provider anyway for degraded mode
        app_instance.state.email_provider = create_email_provider()

    # Create shared HTTP client for auth
    app_instance.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )

    logger.info("✨ Email Service startup complete")
    yield

    logger.info("⏹️ Shutting down Email Service...")

    # Close shared HTTP client
    await app_instance.state.http_client.aclose()

    # Close ARQ queue pool
    try:
        from app.queue import close_queue_pool

        await close_queue_pool()
        logger.info("✅ ARQ queue pool closed")
    except Exception:
        logger.error("Error closing ARQ queue pool", exc_info=True)

    # Disconnect Redis cache
    try:
        cache = getattr(app_instance.state, "cache_manager", None)
        if cache:
            await cache.disconnect()
            logger.info("✅ Redis cache disconnected")
    except Exception:
        logger.error("Error disconnecting from Redis", exc_info=True)

    logger.info("👋 Email Service shutdown complete")


# =============================================================================
# FastAPI Application
# =============================================================================
# No root_path hardcoded - ProxyHeadersMiddleware reads X-Forwarded-Prefix

app = FastAPI(
    title=settings.project_name,
    version=settings.project_version,
    description=settings.project_description,
    docs_url="/docs",
    redoc_url=None,  # Disabled - we only use Swagger UI
    openapi_url="/openapi.json",
    debug=settings.debug,
    lifespan=lifespan,
)

# =============================================================================
# Middleware (order matters - first added = last executed, outermost first)
# =============================================================================

# Proxy headers middleware (outermost - handles X-Forwarded-Prefix for root_path)
# Must be first so it runs before all other middleware
app.add_middleware(ProxyHeadersMiddleware)

# Logging middleware
app.add_middleware(RequestLoggingMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# =============================================================================
# Exception Handlers
# =============================================================================
app.add_exception_handler(ServiceException, service_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(PydanticValidationError, validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# =============================================================================
# Routers
# =============================================================================
app.include_router(emails_router, prefix=f"{settings.api_prefix}/emails")
app.include_router(suppressions_router, prefix=f"{settings.api_prefix}/suppressions")
app.include_router(webhooks_router, prefix=f"{settings.api_prefix}/webhooks")
app.include_router(dashboard_router, prefix="/dashboard")


# ============================================================================
# Health Check (at root level, not under api_prefix)
# ============================================================================


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health Check",
    responses={
        200: {"description": "Service is healthy or degraded"},
        503: {"description": "Service is unhealthy - critical dependencies failed"},
    },
)
async def health_check(request: Request) -> HealthResponse:
    """Check health of the application and its dependencies.

    This endpoint is at the root level for easy access by:
    - Load balancers
    - Kubernetes probes
    - Docker healthchecks
    - Monitoring systems

    Returns:
        - 200 OK: Service is healthy or degraded (can still serve requests)
        - 503 Service Unavailable: Critical dependencies failed (PostgreSQL or Redis)

    Email provider is optional - if unavailable, service is degraded but not unhealthy.
    """
    from fastapi.responses import JSONResponse

    dependencies: dict[str, DependencyStatus] = {}

    # ==========================================================================
    # Critical dependencies (if any fails -> UNHEALTHY -> 503)
    # ==========================================================================

    # Check PostgreSQL (CRITICAL)
    try:
        async with async_session_maker() as session:
            await session.execute(select(1))
        dependencies["postgresql"] = DependencyStatus(
            status=ServiceStatus.HEALTHY,
            latency_ms=0,
        )
    except Exception as e:
        logger.error("DB health check failed: %s", e)
        dependencies["postgresql"] = DependencyStatus(
            status=ServiceStatus.UNHEALTHY,
            error=str(e),
        )

    # Check Redis (CRITICAL - needed for rate limiting, dedup, queue)
    try:
        cache: CacheManager = getattr(request.app.state, "cache_manager", None)
        if cache and await cache.ping():
            dependencies["redis"] = DependencyStatus(
                status=ServiceStatus.HEALTHY,
                latency_ms=0,
            )
        else:
            dependencies["redis"] = DependencyStatus(
                status=ServiceStatus.UNHEALTHY,
                error="Redis not connected",
            )
    except Exception as e:
        dependencies["redis"] = DependencyStatus(
            status=ServiceStatus.UNHEALTHY,
            error=str(e),
        )

    # ==========================================================================
    # Optional dependencies (if unavailable -> DEGRADED, service still works)
    # ==========================================================================

    # Check Email Provider (OPTIONAL - emails will queue but not send)
    try:
        provider = getattr(request.app.state, "email_provider", None)
        if provider:
            available, error = provider.get_status()
            if available:
                dependencies["email_provider"] = DependencyStatus(
                    status=ServiceStatus.HEALTHY,
                    latency_ms=0,
                )
            else:
                dependencies["email_provider"] = DependencyStatus(
                    status=ServiceStatus.DEGRADED,
                    error=error or "Provider not available",
                )
    except Exception as e:
        dependencies["email_provider"] = DependencyStatus(
            status=ServiceStatus.DEGRADED,
            error=str(e),
        )

    # ==========================================================================
    # Determine overall status
    # ==========================================================================

    # Critical dependencies that must be healthy
    critical_deps = ["postgresql", "redis"]
    critical_statuses = [dependencies[dep].status for dep in critical_deps if dep in dependencies]

    # If any critical dependency is unhealthy -> service is unhealthy
    if ServiceStatus.UNHEALTHY in critical_statuses:
        overall_status = ServiceStatus.UNHEALTHY
    # If all critical are healthy but optional are degraded -> degraded
    elif ServiceStatus.DEGRADED in [dep.status for dep in dependencies.values()]:
        overall_status = ServiceStatus.DEGRADED
    # All healthy
    else:
        overall_status = ServiceStatus.HEALTHY

    response_data = HealthResponse(
        status=overall_status,
        version=settings.project_version,
        environment=settings.environment,
        dependencies=dependencies,
    )

    # Return 503 if unhealthy (for Docker/K8s health checks)
    if overall_status == ServiceStatus.UNHEALTHY:
        return JSONResponse(
            status_code=503,
            content=response_data.model_dump(by_alias=True),
        )

    return response_data


@app.get("/", tags=["System"], summary="Root")
async def root():
    """Root endpoint - basic service info."""
    return {
        "service": settings.project_name,
        "version": settings.project_version,
        "docs": "/docs",
        "health": "/health",
        "dashboard": "/dashboard/",
    }


@app.get(
    "/providers",
    tags=["System"],
    summary="Available Providers",
    description="List available email providers.",
)
async def list_providers() -> dict:
    """List available email providers."""
    return {
        "available": get_available_providers(),
        "current": settings.email_provider,
    }


# =============================================================================
# Development Server
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8081,
        reload=settings.debug,
    )
