"""FastAPI dependencies for the application."""

import time
from datetime import datetime
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

import httpx
from fastapi import Depends, Query, Request

if TYPE_CHECKING:
    from app.repository import SuppressionRepository
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_session
from app.exceptions import AuthenticationError, AuthorizationError, ExternalServiceError
from app.logger import get_logger
from app.models import EmailPriority, EmailStatus, EmailType
from app.providers.base import BaseEmailProvider
from app.providers.factory import create_email_provider
from app.repository import BatchRepository, EmailRepository, TemplateRepository
from app.service import EmailService

logger = get_logger(__name__)

_token_cache: dict[str, tuple["TokenData", float]] = {}
_TOKEN_CACHE_TTL = 300
_TOKEN_CACHE_MAX = 500

# ============================================================================
# Authentication Dependencies
# ============================================================================

# tokenUrl is for Swagger UI (browser) - points to identity service login
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=settings.identity_login_url)


class TokenData(BaseModel):
    """Token data from identity service."""

    id: UUID
    user_name: str
    mail: str | None = None
    role: str


async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)) -> TokenData:
    """Get current user from identity service.

    Args:
        request: FastAPI request (for shared HTTP client)
        token: OAuth2 token

    Returns:
        User data from identity service

    Raises:
        AuthenticationError: If token validation fails
    """
    cached = _token_cache.get(token)
    if cached:
        token_data, expiry = cached
        if time.monotonic() < expiry:
            return token_data
        del _token_cache[token]

    client = request.app.state.http_client
    try:
        # GET /me with Bearer token in header
        # Uses identity_me_url for server-to-server communication
        response = await client.get(
            settings.identity_me_url,
            headers={"Authorization": f"Bearer {token}"},
        )

        if response.status_code == 200:
            data = response.json()
            token_data = TokenData(
                id=UUID(data["id"]),
                user_name=data["userName"],
                mail=data.get("mail"),  # Can be None for SERVICE users
                role=data["role"],
            )
            if len(_token_cache) >= _TOKEN_CACHE_MAX:
                _token_cache.clear()
            _token_cache[token] = (token_data, time.monotonic() + _TOKEN_CACHE_TTL)
            return token_data

        error_msg = response.json().get("detail", "Token validation failed")
        raise AuthenticationError(error_msg)

    except httpx.ConnectTimeout:
        logger.error("Identity service connection timeout")
        raise ExternalServiceError(
            "Identity", "Connection timeout"
        )
    except httpx.ReadTimeout:
        logger.error("Identity service read timeout")
        raise ExternalServiceError(
            "Identity", "Response timeout"
        )
    except httpx.ConnectError:
        logger.error("Cannot connect to identity service")
        raise ExternalServiceError(
            "Identity", "Service unavailable"
        )
    except AuthenticationError:
        raise
    except Exception as e:
        logger.error("Identity service error: %s", e)
        raise AuthenticationError(str(e))


async def service_role_required(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Require service role for access.

    Args:
        current_user: Current authenticated user

    Returns:
        User data if authorized

    Raises:
        AuthorizationError: If user doesn't have service role
    """
    if current_user.role != "service":
        raise AuthorizationError("Service privileges required")
    return current_user


async def admin_role_required(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Require admin role for access."""
    if current_user.role not in ["admin", "service"]:
        raise AuthorizationError("Admin privileges required")
    return current_user


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Get shared HTTP client from app state."""
    return request.app.state.http_client


# ============================================================================
# Service Dependencies (from app.state — created once in lifespan)
# ============================================================================


def get_email_provider(request: Request) -> BaseEmailProvider:
    """Get email provider from app state."""
    return request.app.state.email_provider


async def get_repository(
    session: AsyncSession = Depends(get_session),
) -> EmailRepository:
    """Get EmailRepository with database session."""
    return EmailRepository(session=session)


async def get_template_repository(
    session: AsyncSession = Depends(get_session),
) -> TemplateRepository:
    """Get TemplateRepository with database session."""
    return TemplateRepository(session=session)


async def get_suppression_repository(
    session: AsyncSession = Depends(get_session),
) -> "SuppressionRepository":
    """Get SuppressionRepository with database session."""
    from app.repository import SuppressionRepository

    return SuppressionRepository(session=session)


async def get_batch_repository(
    session: AsyncSession = Depends(get_session),
) -> BatchRepository:
    """Get BatchRepository with database session."""
    return BatchRepository(session=session)


async def get_cache_manager(request: Request):
    """Get CacheManager from app state."""
    from app.cache import CacheManager

    cache: CacheManager | None = getattr(request.app.state, "cache_manager", None)
    return cache


async def get_email_service(
    request: Request,
    repo: EmailRepository = Depends(get_repository),
    template_repo: TemplateRepository = Depends(get_template_repository),
    provider: BaseEmailProvider = Depends(get_email_provider),
    suppression_repo: "SuppressionRepository" = Depends(get_suppression_repository),
    batch_repo: BatchRepository = Depends(get_batch_repository),
) -> EmailService:
    """Get EmailService with all dependencies including cache."""
    cache = await get_cache_manager(request)
    return EmailService(
        repo=repo,
        provider=provider,
        template_repo=template_repo,
        cache=cache,
        suppression_repo=suppression_repo,
        batch_repo=batch_repo,
    )


# ============================================================================
# Pagination Parameters
# ============================================================================


class PaginationParams(BaseModel):
    """Pagination parameters."""

    skip: int = Field(default=0, ge=0, description="Offset")
    limit: int = Field(default=20, ge=1, le=100, description="Page size")

    @property
    def page(self) -> int:
        """Calculate page number (1-indexed)."""
        return (self.skip // self.limit) + 1 if self.limit > 0 else 1


def get_pagination_params(
    skip: Annotated[int, Query(ge=0, description="Offset")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Limit")] = 20,
) -> PaginationParams:
    """Dependency for pagination parameters."""
    return PaginationParams(skip=skip, limit=limit)


# ============================================================================
# Email Filter Parameters
# ============================================================================


ALLOWED_SORT_FIELDS = {
    "created_at",
    "sent_at",
    "status",
    "subject",
    "source_service",
    "priority",
}


class EmailFilterParams(BaseModel):
    """Email filter parameters."""

    status: EmailStatus | None = None
    priority: EmailPriority | None = None
    email_type: EmailType | None = None
    source_service: str | None = None
    search: str | None = None
    to_email: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    sort_by: str = "created_at"
    sort_order: str = "desc"


def get_email_filters(
    status: Annotated[
        EmailStatus | None,
        Query(description="Filter by status"),
    ] = None,
    priority: Annotated[
        EmailPriority | None,
        Query(description="Filter by priority"),
    ] = None,
    email_type: Annotated[
        EmailType | None,
        Query(description="Filter by email type"),
    ] = None,
    source_service: Annotated[
        str | None,
        Query(description="Filter by source service"),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search by ID or subject (partial match)"),
    ] = None,
    to_email: Annotated[
        str | None,
        Query(description="Filter by recipient email (partial match)"),
    ] = None,
    date_from: Annotated[
        datetime | None,
        Query(description="Filter emails created after this datetime (ISO 8601)"),
    ] = None,
    date_to: Annotated[
        datetime | None,
        Query(description="Filter emails created before this datetime (ISO 8601)"),
    ] = None,
    sort_by: Annotated[
        str,
        Query(description="Sort field (created_at, sent_at, status, subject, source_service, priority)"),
    ] = "created_at",
    sort_order: Annotated[
        str,
        Query(description="Sort order: asc or desc"),
    ] = "desc",
) -> EmailFilterParams:
    """Dependency for email filter parameters."""
    if sort_by not in ALLOWED_SORT_FIELDS:
        sort_by = "created_at"
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"
    return EmailFilterParams(
        status=status,
        priority=priority,
        email_type=email_type,
        source_service=source_service,
        search=search.strip() if search else None,
        to_email=to_email.strip() if to_email else None,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )
