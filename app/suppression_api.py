"""API endpoints for suppression list management."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.dependencies import (
    TokenData,
    admin_role_required,
    get_suppression_repository,
    service_role_required,
)
from app.logger import get_logger
from app.models import SuppressionReason
from app.repository import SuppressionRepository
from app.schemas import (
    PaginatedResponse,
    PaginationMeta,
    SuppressionCheck,
    SuppressionCreate,
    SuppressionResponse,
    SuppressionStats,
)

router = APIRouter(tags=["Suppressions"])
logger = get_logger(__name__)


# ============================================================================
# Suppression CRUD
# ============================================================================


@router.get(
    "",
    response_model=PaginatedResponse[SuppressionResponse],
    summary="List suppressed emails",
)
async def list_suppressions(
    current_user: TokenData = Depends(service_role_required),
    repo: SuppressionRepository = Depends(get_suppression_repository),
    reason: SuppressionReason | None = Query(default=None, description="Filter by reason"),
    active_only: bool = Query(default=True, description="Only active suppressions"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> PaginatedResponse[SuppressionResponse]:
    """Get list of suppressed emails with optional filtering."""
    suppressions = await repo.get_all(
        active_only=active_only,
        reason=reason,
        limit=limit,
        offset=skip,
    )

    total = await repo.count(active_only=active_only, reason=reason)

    return PaginatedResponse(
        data=[SuppressionResponse.model_validate(s) for s in suppressions],
        meta=PaginationMeta.from_params(total=total, skip=skip, limit=limit),
    )


@router.get(
    "/stats",
    response_model=SuppressionStats,
    summary="Get suppression statistics",
)
async def get_suppression_stats(
    current_user: TokenData = Depends(service_role_required),
    repo: SuppressionRepository = Depends(get_suppression_repository),
) -> SuppressionStats:
    """Get statistics about the suppression list."""
    stats = await repo.get_stats()
    return SuppressionStats(**stats)


@router.get(
    "/check/{email}",
    response_model=SuppressionCheck,
    summary="Check if email is suppressed",
)
async def check_suppression(
    email: Annotated[str, Path(description="Email to check")],
    current_user: TokenData = Depends(service_role_required),
    repo: SuppressionRepository = Depends(get_suppression_repository),
) -> SuppressionCheck:
    """Check if a specific email address is in the suppression list."""
    suppression = await repo.is_suppressed(email)

    if suppression:
        return SuppressionCheck(
            email=email.lower(),
            is_suppressed=True,
            reason=suppression.reason,
            description=suppression.description,
            suppressed_at=suppression.created_at,
        )

    return SuppressionCheck(
        email=email.lower(),
        is_suppressed=False,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SuppressionResponse,
    summary="Add email to suppression list",
)
async def add_suppression(
    data: SuppressionCreate,
    current_user: TokenData = Depends(admin_role_required),
    repo: SuppressionRepository = Depends(get_suppression_repository),
) -> SuppressionResponse:
    """Manually add an email to the suppression list (admin only)."""
    suppression = await repo.add(
        email=data.email,
        reason=data.reason,
        source="manual",
        description=data.description,
        created_by=current_user.user_name,
    )

    logger.info(
        "Admin added email to suppression list",
        extra={
            "extra_fields": {
                "email": data.email,
                "reason": data.reason.value,
                "admin": current_user.user_name,
            }
        },
    )

    return SuppressionResponse.model_validate(suppression)


@router.delete(
    "/{email}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove email from suppression list",
)
async def remove_suppression(
    email: Annotated[str, Path(description="Email to remove")],
    current_user: TokenData = Depends(admin_role_required),
    repo: SuppressionRepository = Depends(get_suppression_repository),
) -> None:
    """Remove an email from the suppression list (admin only).

    This deactivates the suppression, allowing future emails to be sent.
    """
    removed = await repo.remove(email)

    if removed:
        logger.info(
            "Admin removed email from suppression list",
            extra={
                "extra_fields": {
                    "email": email,
                    "admin": current_user.user_name,
                }
            },
        )
