"""Custom exceptions and handlers for the Mail Service."""

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.logger import get_logger
from app.schemas import ErrorResponse

logger = get_logger(__name__)


# ============================================================================
# Custom Exceptions
# ============================================================================


class ServiceException(Exception):
    """Base service exception."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class EntityNotFoundError(ServiceException):
    """Entity not found."""

    def __init__(self, entity: str, identifier: str | int):
        super().__init__(
            message=f"{entity} '{identifier}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"entity": entity, "identifier": str(identifier)},
        )


class EntityAlreadyExistsError(ServiceException):
    """Entity already exists."""

    def __init__(self, entity: str, identifier: str | int):
        super().__init__(
            message=f"{entity} '{identifier}' already exists",
            status_code=status.HTTP_409_CONFLICT,
            details={"entity": entity, "identifier": str(identifier)},
        )


class AuthenticationError(ServiceException):
    """Authentication failed."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            details={"www_authenticate": "Bearer"},
        )


class AuthorizationError(ServiceException):
    """Authorization failed."""

    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(message=message, status_code=status.HTTP_403_FORBIDDEN)


class DatabaseError(ServiceException):
    """Database operation failed."""

    def __init__(self, operation: str, message: str):
        super().__init__(
            message=f"Database {operation} failed: {message}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details={"operation": operation},
        )


class ExternalServiceError(ServiceException):
    """External service call failed."""

    def __init__(self, service: str, message: str):
        super().__init__(
            message=f"{service} error: {message}",
            status_code=status.HTTP_502_BAD_GATEWAY,
            details={"service": service},
        )


class EntityInactiveError(ServiceException):
    """Entity is inactive."""

    def __init__(self, entity: str, identifier: str | int):
        super().__init__(
            message=f"{entity} '{identifier}' is inactive",
            status_code=status.HTTP_403_FORBIDDEN,
            details={"entity": entity, "identifier": str(identifier)},
        )


class ValidationError(ServiceException):
    """Validation failed."""

    def __init__(self, message: str):
        super().__init__(
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


# ============================================================================
# Email-Specific Exceptions
# ============================================================================


class EmailError(ServiceException):
    """Base email exception."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details=details or {},
        )


class EmailProviderError(EmailError):
    """Email provider failed."""

    def __init__(self, provider: str, message: str):
        super().__init__(
            message=f"Email provider '{provider}' failed: {message}",
            details={"provider": provider},
        )


class EmailConnectionError(EmailError):
    """Cannot connect to email provider."""

    def __init__(self, provider: str, message: str = "Connection failed"):
        super().__init__(
            message=f"Cannot connect to '{provider}': {message}",
            details={"provider": provider},
        )


class EmailSendError(EmailError):
    """Failed to send email."""

    def __init__(self, recipient: str, reason: str):
        super().__init__(
            message=f"Failed to send email to '{recipient}': {reason}",
            details={"recipient": recipient, "reason": reason},
        )


class AttachmentError(EmailError):
    """Attachment processing failed."""

    def __init__(self, message: str):
        super().__init__(
            message=message,
            details={},
        )
        self.status_code = status.HTTP_400_BAD_REQUEST


class RateLimitError(ServiceException):
    """Rate limit exceeded."""

    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(
            message=message,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details={"retry_after": 3600},
        )


class DuplicateEmailError(ServiceException):
    """Duplicate email detected."""

    def __init__(self, message: str = "Duplicate email detected"):
        super().__init__(
            message=message,
            status_code=status.HTTP_409_CONFLICT,
            details={"reason": "duplicate"},
        )


class SuppressionError(ServiceException):
    """Email address is in suppression list."""

    def __init__(
        self,
        message: str = "Email address is suppressed",
        suppressed_emails: list[dict] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details={"suppressed_emails": suppressed_emails or []},
        )


class InvalidProviderError(EmailError):
    """Invalid email provider configuration."""

    def __init__(self, provider: str, available: list[str]):
        super().__init__(
            message=f"Unknown email provider: '{provider}'. Available: {', '.join(available)}",
            details={"provider": provider, "available_providers": available},
        )


# ============================================================================
# Exception Handlers
# ============================================================================


async def service_exception_handler(
    _request: Request, exc: ServiceException
) -> JSONResponse:
    """Handle service exceptions."""
    logger.warning(
        f"Service exception: {exc.message}",
        extra={
            "extra_fields": {
                "status_code": exc.status_code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(message=exc.message, details=exc.details).model_dump(),
    )


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError | PydanticValidationError
) -> JSONResponse:
    """Handle validation errors."""
    errors = exc.errors() if hasattr(exc, "errors") else []
    cleaned_errors = [
        {"loc": e.get("loc", []), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in errors
    ]
    logger.warning(
        "Validation error",
        extra={
            "extra_fields": {
                "error_count": len(cleaned_errors),
                "errors": cleaned_errors[:3],
            }
        },
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            message="Validation error", details={"errors": cleaned_errors}
        ).model_dump(),
    )


async def sqlalchemy_exception_handler(
    _request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    """Handle database errors."""
    logger.error(
        "Database error",
        extra={
            "extra_fields": {
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }
        },
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            message="Database error", details={"type": exc.__class__.__name__}
        ).model_dump(),
    )


async def general_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected errors."""
    logger.error(
        "Unexpected error",
        extra={
            "extra_fields": {
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }
        },
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            message="An unexpected error occurred",
            details={"type": exc.__class__.__name__},
        ).model_dump(),
    )
