"""Base schemas: CamelModel, pagination, and error responses."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# Base Schema with camelCase serialization
# ============================================================================


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase.

    Example: user_name -> userName, created_at -> createdAt
    """
    components = string.split("_")
    return components[0] + "".join(word.capitalize() for word in components[1:])


class CamelModel(BaseModel):
    """Base model that serializes to camelCase for API responses.

    - Internal code uses snake_case (Pythonic)
    - API responses use camelCase (JavaScript convention)
    - API requests accept both snake_case and camelCase
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,  # Accept both snake_case and camelCase in requests
        from_attributes=True,  # Allow creating from ORM models
    )


# ============================================================================
# Error Response
# ============================================================================


class ErrorResponse(CamelModel):
    """Standard error response."""

    success: bool = False
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Pagination
# ============================================================================


class PaginationMeta(CamelModel):
    """Pagination metadata."""

    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool

    @classmethod
    def from_params(cls, total: int, skip: int, limit: int) -> "PaginationMeta":
        """Create pagination meta from query params."""
        page = (skip // limit) + 1 if limit > 0 else 1
        total_pages = (total + limit - 1) // limit if limit > 0 else 1
        return cls(
            total=total,
            page=page,
            page_size=limit,
            total_pages=total_pages,
            has_next=skip + limit < total,
            has_previous=skip > 0,
        )


class PaginatedResponse[T](CamelModel):
    """Paginated response wrapper."""

    data: list[T]
    meta: PaginationMeta
