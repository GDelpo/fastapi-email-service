"""API endpoints for emails - Simplified REST API."""


import json
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)
from fastapi.responses import FileResponse, Response

from app.config import settings
from app.dependencies import (
    EmailFilterParams,
    PaginationParams,
    TokenData,
    admin_role_required,
    get_email_filters,
    get_email_service,
    get_pagination_params,
    service_role_required,
)
from app.logger import get_logger
from app.schemas import (
    AttachmentCreate,
    BulkEmailCreate,
    BulkEmailResponse,
    EmailCreate,
    EmailEventResponse,
    EmailResponse,
    EmailSummary,
    PaginatedResponse,
    PaginationMeta,
    QueueStats,
)
from app.service import EmailService
from app.utils import (
    AttachmentValidationError,
    decode_base64_async,
    validate_and_process_attachments,
    validate_attachments_count,
    validate_content_type,
    validate_file_size,
    validate_total_size,
)

router = APIRouter(tags=["Emails"])
logger = get_logger(__name__)


def _derive_source_service(current_user: TokenData) -> str:
    """Derive source_service identifier from token.

    - If role is 'service', use user_name as service identifier (e.g., 'gateway', 'crm')
    - Otherwise, it's a user calling via API directly
    """
    return current_user.user_name if current_user.role == "service" else "api"


# ============================================================================
# Email CRUD
# ============================================================================


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EmailResponse,
    summary="Create and queue email",
    description=f"""Create an email and queue it for background sending.

**Attachment limits:**
- Max file size: {settings.max_attachment_size_mb} MB per file
- Max total size: {settings.max_total_attachments_size_mb} MB
- Max attachments: {settings.max_attachments_count} files

**Supports two content types:**

1. **JSON** (`Content-Type: application/json`):
   ```json
   {{
     "to": [{{"email": "user@example.com"}}],
     "subject": "Hello",
     "html_content": "<p>Hi</p>",
     "attachments": [{{
       "filename": "doc.pdf",
       "content_type": "application/pdf",
       "content_base64": "JVBERi0xLjQK..."
     }}]
   }}
   ```

2. **Multipart** (`Content-Type: multipart/form-data`):
   ```bash
   curl -X POST /emails \\
     -F 'email={{"to":[{{"email":"user@example.com"}}],"subject":"Hello"}}' \\
     -F "files=@document.pdf"
   ```
""",
    responses={
        200: {"description": "Email sent (sync=true)"},
        202: {"description": "Email queued for background processing"},
        400: {"description": "Invalid request or attachment validation failed"},
    },
)
async def create_email(
    request: Request,
    sync: Annotated[
        bool,
        Query(description="Send immediately instead of queuing"),
    ] = False,
    current_user: TokenData = Depends(service_role_required),
    service: EmailService = Depends(get_email_service),
) -> EmailResponse:
    """Create and send/queue email.

    Accepts both JSON and multipart/form-data requests.

    - **sync=false** (default): Queue for background processing (202 Accepted)
    - **sync=true**: Send immediately and wait for result (200 OK)

    Requires service role.
    """
    content_type = request.headers.get("content-type", "")

    try:
        if "multipart/form-data" in content_type:
            email_data, files_count = await _handle_multipart_request(request)
            request_type = "multipart"
        else:
            email_data = await _handle_json_request(request)
            request_type = "json"
            files_count = 0

            # Validate JSON attachments if present
            if email_data.attachments:
                await _validate_json_attachments(email_data.attachments)

    except AttachmentValidationError as e:
        logger.warning(
            f"❌ Attachment validation failed: {e.message}",
            extra={"extra_fields": {"filename": e.filename}},
        )
        raise HTTPException(status_code=400, detail=e.message)

    source_service = _derive_source_service(current_user)

    logger.info(
        f"📧 Email request ({request_type})",
        extra={
            "extra_fields": {
                "requester": current_user.user_name,
                "source_service": source_service,
                "priority": email_data.priority.value,
                "sync": sync,
                "files_uploaded": files_count,
                "attachments": len(email_data.attachments)
                if email_data.attachments
                else 0,
            }
        },
    )

    email = await service.create_email(
        email_data=email_data,
        owner_id=current_user.id,
        source_service=source_service,
        sync=sync,
    )

    logger.info(
        f"✅ Email {'sent' if sync else 'queued'}",
        extra={
            "extra_fields": {
                "email_id": str(email.id),
                "status": email.status.value,
                "requester": current_user.user_name,
            }
        },
    )

    return EmailResponse.model_validate(email)


# ============================================================================
# Bulk Email Endpoint
# ============================================================================


@router.post(
    "/bulk",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BulkEmailResponse,
    summary="Create bulk email batch",
    description="""Create and queue multiple emails for background sending.

**Use cases:**
- Newsletter to many subscribers
- Marketing campaigns
- Bulk notifications

**Content options (provide one):**
- `template_id`: Use a saved template with variable substitution
- `subject` + `html_content`: Inline content with Jinja2 variables

**Variables:**
- Each recipient can have custom variables: `{"name": "John", "code": "ABC123"}`
- Use in templates: `Hello {{name}}, your code is {{code}}`
- Built-in variables: `{{email}}`, `{{name}}` (auto-extracted from recipient)

**Example request:**
```json
{
  "template_id": "uuid-or-null",
  "subject": "Hello {{name}}!",
  "html_content": "<p>Hi {{name}}, your code is {{code}}</p>",
  "from_email": "newsletter@example.com",
  "recipients": [
    {"email": "user1@test.com", "name": "Juan", "variables": {"code": "ABC123"}},
    {"email": "user2@test.com", "name": "María", "variables": {"code": "XYZ789"}}
  ],
  "skip_suppressed": true
}
```

**Limits:**
- Max 1000 recipients per batch
- Rate limited per caller
""",
    responses={
        202: {"description": "Batch created and emails queued"},
        400: {"description": "Invalid request"},
        422: {"description": "Validation error or all recipients suppressed"},
    },
)
async def create_bulk_email(
    bulk_data: BulkEmailCreate,
    current_user: TokenData = Depends(service_role_required),
    service: EmailService = Depends(get_email_service),
) -> BulkEmailResponse:
    """Create bulk email batch.

    Creates a batch of emails for multiple recipients with optional
    personalization per recipient.

    Requires service role.
    """
    source_service = _derive_source_service(current_user)

    logger.info(
        "📨 Bulk email request",
        extra={
            "extra_fields": {
                "requester": current_user.user_name,
                "recipients": len(bulk_data.recipients),
                "priority": bulk_data.priority.value,
                "has_template": bulk_data.template_id is not None,
            }
        },
    )

    result = await service.create_bulk_email(
        bulk_data=bulk_data,
        owner_id=current_user.id,
        source_service=source_service,
    )

    logger.info(
        "✅ Bulk batch created",
        extra={
            "extra_fields": {
                "batch_id": str(result.batch_id),
                "queued": result.queued,
                "skipped": result.skipped,
            }
        },
    )

    return result


@router.get(
    "",
    response_model=PaginatedResponse[EmailSummary],
    summary="List emails",
    description="Get paginated list of emails with optional filtering.",
)
async def list_emails(
    pagination: PaginationParams = Depends(get_pagination_params),
    filters: EmailFilterParams = Depends(get_email_filters),
    current_user: TokenData = Depends(admin_role_required),
    service: EmailService = Depends(get_email_service),
) -> PaginatedResponse[EmailSummary]:
    """List emails with pagination and filters.

    Requires admin role."""
    logger.debug(
        "📋 Listing emails",
        extra={
            "extra_fields": {
                "requester": current_user.user_name,
                "skip": pagination.skip,
                "limit": pagination.limit,
                "status_filter": filters.status.value if filters.status else None,
            }
        },
    )

    emails = await service.list_emails(
        status=filters.status,
        priority=filters.priority,
        email_type=filters.email_type,
        source_service=filters.source_service,
        search=filters.search,
        to_email=filters.to_email,
        date_from=filters.date_from,
        date_to=filters.date_to,
        skip=pagination.skip,
        limit=pagination.limit,
        sort_by=filters.sort_by,
        sort_order=filters.sort_order,
    )

    total = await service.count_emails(
        status=filters.status,
        priority=filters.priority,
        email_type=filters.email_type,
        source_service=filters.source_service,
        search=filters.search,
        to_email=filters.to_email,
        date_from=filters.date_from,
        date_to=filters.date_to,
    )
    meta = PaginationMeta.from_params(total, pagination.skip, pagination.limit)

    summaries = [EmailSummary.model_validate(e) for e in emails]
    return PaginatedResponse(data=summaries, meta=meta)


@router.get(
    "/stats",
    response_model=QueueStats,
    summary="Get queue statistics",
)
async def get_queue_stats(
    current_user: TokenData = Depends(admin_role_required),
    service: EmailService = Depends(get_email_service),
) -> QueueStats:
    """Get email queue statistics.

    Requires admin role.
    """
    return await service.get_queue_stats()


@router.get(
    "/{email_id}",
    response_model=EmailResponse,
    summary="Get email by ID",
)
async def get_email(
    email_id: Annotated[UUID, Path(..., description="Email UUID")],
    include: Annotated[
        str | None,
        Query(
            description="Include related data: events, attachments (comma-separated)"
        ),
    ] = None,
    current_user: TokenData = Depends(admin_role_required),
    service: EmailService = Depends(get_email_service),
) -> EmailResponse:
    """Get email details by ID.

    Requires admin role.

    Use ?include=events,attachments to include related data.
    """
    logger.debug(
        "🔍 Getting email",
        extra={
            "extra_fields": {
                "email_id": str(email_id),
                "requester": current_user.user_name,
            }
        },
    )

    # Parse include param
    include_details = bool(
        include and any(x in include for x in ["events", "attachments"])
    )

    email = await service.get_email(email_id, with_details=include_details)
    return EmailResponse.model_validate(email)


@router.post(
    "/{email_id}/retry",
    response_model=EmailResponse,
    summary="Retry failed email",
)
async def retry_email(
    email_id: Annotated[UUID, Path(..., description="Email UUID")],
    reset: Annotated[
        bool,
        Query(description="Reset retry counter"),
    ] = False,
    current_user: TokenData = Depends(admin_role_required),
    service: EmailService = Depends(get_email_service),
) -> EmailResponse:
    """Retry sending a failed email.

    Requires admin role.
    """
    logger.info(
        "🔄 Retrying email",
        extra={
            "extra_fields": {
                "email_id": str(email_id),
                "reset_count": reset,
                "requester": current_user.user_name,
            }
        },
    )
    email = await service.retry_email(email_id, reset_count=reset)
    return EmailResponse.model_validate(email)


@router.delete(
    "/{email_id}",
    response_model=EmailResponse,
    summary="Cancel email",
)
async def cancel_email(
    email_id: Annotated[UUID, Path(..., description="Email UUID")],
    reason: Annotated[
        str | None,
        Query(description="Cancellation reason"),
    ] = None,
    current_user: TokenData = Depends(admin_role_required),
    service: EmailService = Depends(get_email_service),
) -> EmailResponse:
    """Cancel a queued email.

    Requires admin role.
    """
    logger.info(
        "❌ Cancelling email",
        extra={
            "extra_fields": {
                "email_id": str(email_id),
                "reason": reason,
                "requester": current_user.user_name,
            }
        },
    )
    email = await service.cancel_email(email_id, reason=reason)
    return EmailResponse.model_validate(email)


# ============================================================================
# Email Events (sub-resource)
# ============================================================================


@router.get(
    "/{email_id}/events",
    response_model=list[EmailEventResponse],
    summary="Get email event timeline",
)
async def get_email_events(
    email_id: Annotated[UUID, Path(..., description="Email UUID")],
    current_user: TokenData = Depends(admin_role_required),
    service: EmailService = Depends(get_email_service),
) -> list[EmailEventResponse]:
    """Get event timeline for an email.

    Requires admin role."""
    events = await service.get_events(email_id)
    return [EmailEventResponse.model_validate(e) for e in events]


@router.get(
    "/{email_id}/attachments/{attachment_id}/download",
    summary="Download email attachment",
    responses={
        200: {
            "description": "Attachment file",
            "content": {"application/octet-stream": {}},
        },
        404: {"description": "Attachment not found"},
    },
)
async def download_attachment(
    email_id: Annotated[UUID, Path(..., description="Email UUID")],
    attachment_id: Annotated[UUID, Path(..., description="Attachment UUID")],
    current_user: TokenData = Depends(admin_role_required),
    service: EmailService = Depends(get_email_service),
):
    """Download an email attachment.

    Requires admin role.

    Returns the file from disk if available, otherwise decodes from base64.
    """
    attachment, file_path = await service.get_attachment(email_id, attachment_id)

    # Try to serve from disk first
    if file_path and file_path.exists():
        return FileResponse(
            path=str(file_path),
            filename=attachment.filename,
            media_type=attachment.content_type,
        )

    # Fall back to base64 decode
    if attachment.content_base64:
        content = await decode_base64_async(attachment.content_base64)
        return Response(
            content=content,
            media_type=attachment.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{attachment.filename}"'
            },
        )

    raise HTTPException(
        status_code=404,
        detail="Attachment content not available",
    )


# ============================================================================
# Helper Functions
# ============================================================================


async def _handle_json_request(request: Request) -> EmailCreate:
    """Parse and validate JSON request body."""
    try:
        body = await request.json()
        return EmailCreate.model_validate(body)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid email data: {e}")


async def _handle_multipart_request(request: Request) -> tuple[EmailCreate, int]:
    """Parse multipart form data with files.

    Returns:
        Tuple of (EmailCreate, files_count)
    """
    form = await request.form()

    # Get email JSON from form
    email_json = form.get("email")
    if not email_json:
        raise HTTPException(
            status_code=400, detail="Missing 'email' field in form data"
        )

    try:
        email_dict = json.loads(str(email_json))
        email_data = EmailCreate.model_validate(email_dict)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid email JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid email data: {e}")

    # Collect uploaded files
    uploaded_files = [
        v for k, v in form.multi_items() if k == "files" and hasattr(v, "read")
    ]

    if uploaded_files:
        # Validate and process files using async utils
        processed, total_size = await validate_and_process_attachments(
            files=uploaded_files,
            existing_attachments=email_data.attachments,
        )

        # Convert to AttachmentCreate and merge with existing
        attachments = list(email_data.attachments) if email_data.attachments else []
        for p in processed:
            attachments.append(
                AttachmentCreate(
                    filename=p.filename,
                    content_type=p.content_type,
                    content_base64=p.content_base64,
                    is_inline=False,
                )
            )
        email_data.attachments = attachments

    return email_data, len(uploaded_files)


async def _validate_json_attachments(attachments: list[AttachmentCreate]) -> None:
    """Validate attachments that come as base64 in JSON body."""
    # Validate count
    validate_attachments_count(len(attachments))

    total_size = 0

    for att in attachments:
        # Validate content type
        validate_content_type(att.content_type, att.filename)

        # Decode to get actual size and validate
        try:
            content = await decode_base64_async(att.content_base64)
        except Exception as e:
            raise AttachmentValidationError(
                f"Invalid base64 content in attachment '{att.filename}': {e}"
            ) from e
        size_bytes = len(content)

        validate_file_size(size_bytes, att.filename)
        total_size += size_bytes

    # Validate total size
    validate_total_size(total_size)
