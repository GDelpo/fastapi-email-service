"""Schemas package: Pydantic DTOs for API requests and responses.

Re-exports all schemas for backward-compatible imports:
    from app.schemas import EmailCreate, EmailResponse, ...
"""

from app.models import (
    BounceType,
    EmailPriority,
    EmailStatus,
    EmailType,
    EventType,
    SuppressionReason,
)
from app.schemas.bulk import (
    BatchDetailResponse,
    BatchSummary,
    BulkEmailCreate,
    BulkEmailResponse,
    BulkRecipient,
    QueuedRecipient,
    SkippedRecipient,
)
from app.schemas.common import (
    CamelModel,
    ErrorResponse,
    PaginatedResponse,
    PaginationMeta,
    to_camel,
)
from app.schemas.email import (
    AttachmentCreate,
    AttachmentResponse,
    EmailCreate,
    EmailEventResponse,
    EmailRecipient,
    EmailResponse,
    EmailSummary,
    EmailUpdate,
    ProcessPendingResponse,
    QueueStats,
)
from app.schemas.health import DependencyStatus, HealthResponse, ServiceStatus
from app.schemas.provider import ProviderCapabilities, ProviderStatus
from app.schemas.suppression import (
    SuppressionCheck,
    SuppressionCreate,
    SuppressionResponse,
    SuppressionStats,
)
from app.schemas.template import TemplateCreate, TemplateResponse, TemplateUpdate
from app.schemas.webhook import EmailProvider, WebhookEvent

__all__ = [
    # Base
    "CamelModel",
    "to_camel",
    "ErrorResponse",
    "PaginatedResponse",
    "PaginationMeta",
    # Enums (re-exported from models)
    "BounceType",
    "EmailPriority",
    "EmailProvider",
    "EmailStatus",
    "EmailType",
    "EventType",
    "SuppressionReason",
    # Health
    "DependencyStatus",
    "HealthResponse",
    "ServiceStatus",
    # Email
    "AttachmentCreate",
    "AttachmentResponse",
    "EmailCreate",
    "EmailEventResponse",
    "EmailRecipient",
    "EmailResponse",
    "EmailSummary",
    "EmailUpdate",
    "ProcessPendingResponse",
    "QueueStats",
    # Template
    "TemplateCreate",
    "TemplateResponse",
    "TemplateUpdate",
    # Suppression
    "SuppressionCheck",
    "SuppressionCreate",
    "SuppressionResponse",
    "SuppressionStats",
    # Webhook
    "WebhookEvent",
    # Provider
    "ProviderCapabilities",
    "ProviderStatus",
    # Bulk
    "BatchDetailResponse",
    "BatchSummary",
    "BulkEmailCreate",
    "BulkEmailResponse",
    "BulkRecipient",
    "QueuedRecipient",
    "SkippedRecipient",
]
