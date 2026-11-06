"""Utility functions for file handling and validation."""

import asyncio
import base64
import mimetypes
from dataclasses import dataclass
from typing import BinaryIO

from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)


class AttachmentValidationError(Exception):
    """Raised when attachment validation fails."""

    def __init__(self, message: str, filename: str | None = None):
        self.message = message
        self.filename = filename
        super().__init__(message)


@dataclass
class ProcessedAttachment:
    """Result of processing an uploaded file."""

    filename: str
    content_type: str
    content_base64: str
    size_bytes: int


async def read_file_async(file: BinaryIO) -> bytes:
    """Read file content asynchronously.

    Uses asyncio.to_thread to avoid blocking the event loop
    for potentially large file reads.
    """
    return await asyncio.to_thread(file.read)


async def encode_base64_async(content: bytes) -> str:
    """Encode bytes to base64 string asynchronously.

    Uses asyncio.to_thread for CPU-bound base64 encoding
    of large files.
    """
    return await asyncio.to_thread(lambda: base64.b64encode(content).decode("utf-8"))


async def decode_base64_async(content_b64: str) -> bytes:
    """Decode base64 string to bytes asynchronously.

    Handles base64 strings with incorrect/missing padding and
    stray whitespace characters that some clients (e.g. PHP)
    may include in their base64 output.
    """

    def _decode(data: str) -> bytes:
        import re
        from urllib.parse import unquote

        # Step 0: URL-decode if needed. Some legacy clients (e.g. Guzzle 5 /
        # PHP) may URL-encode the base64 padding '=' as '%3D', '+' as '%2B', etc.
        if "%" in data:
            data = unquote(data)

        # Fast path: try strict decode first (works for well-formed base64
        # from modern clients). This is the expected path for new apps.
        try:
            return base64.b64decode(data, validate=True)
        except Exception:
            pass

        # Fallback: clean up base64 from legacy/non-standard clients.
        # Remove everything except valid base64 alphabet + padding.
        cleaned = re.sub(r"[^A-Za-z0-9+/=]", "", data)
        return base64.b64decode(cleaned, validate=False)

    return await asyncio.to_thread(lambda: _decode(content_b64))


def validate_content_type(content_type: str, filename: str | None = None) -> None:
    """Validate that content type is allowed.

    Args:
        content_type: MIME type of the file
        filename: Optional filename for error messages

    Raises:
        AttachmentValidationError: If content type is not allowed
    """
    allowed_types = settings.allowed_attachment_types_list

    # Also check by extension if content_type is generic
    if content_type == "application/octet-stream" and filename:
        guessed_type, _ = mimetypes.guess_type(filename)
        if guessed_type:
            content_type = guessed_type

    if content_type not in allowed_types:
        raise AttachmentValidationError(
            f"File type '{content_type}' is not allowed. "
            f"Allowed types: {', '.join(allowed_types[:5])}...",
            filename=filename,
        )


def validate_file_size(size_bytes: int, filename: str | None = None) -> None:
    """Validate that file size is within limits.

    Args:
        size_bytes: Size of the file in bytes
        filename: Optional filename for error messages

    Raises:
        AttachmentValidationError: If file is too large
    """
    max_size = settings.max_attachment_size_bytes

    if size_bytes > max_size:
        size_mb = size_bytes / (1024 * 1024)
        raise AttachmentValidationError(
            f"File size ({size_mb:.2f} MB) exceeds maximum allowed "
            f"({settings.max_attachment_size_mb} MB)",
            filename=filename,
        )


def validate_attachments_count(count: int) -> None:
    """Validate that number of attachments is within limits.

    Args:
        count: Number of attachments

    Raises:
        AttachmentValidationError: If too many attachments
    """
    max_count = settings.max_attachments_count

    if count > max_count:
        raise AttachmentValidationError(
            f"Too many attachments ({count}). Maximum allowed: {max_count}"
        )


def validate_total_size(total_bytes: int) -> None:
    """Validate that total size of all attachments is within limits.

    Args:
        total_bytes: Total size of all attachments in bytes

    Raises:
        AttachmentValidationError: If total size exceeds limit
    """
    max_total = settings.max_total_attachments_size_bytes

    if total_bytes > max_total:
        total_mb = total_bytes / (1024 * 1024)
        raise AttachmentValidationError(
            f"Total attachments size ({total_mb:.2f} MB) exceeds maximum "
            f"({settings.max_total_attachments_size_mb} MB)"
        )


async def process_uploaded_file(
    file,
    filename: str | None = None,
    content_type: str | None = None,
) -> ProcessedAttachment:
    """Process an uploaded file: validate, read, and encode.

    Args:
        file: File-like object with read() method
        filename: Original filename
        content_type: MIME type of the file

    Returns:
        ProcessedAttachment with validated and encoded content

    Raises:
        AttachmentValidationError: If validation fails
    """
    # Get metadata
    actual_filename = filename or getattr(file, "filename", None) or "attachment"
    actual_content_type = (
        content_type or getattr(file, "content_type", None) or "application/octet-stream"
    )

    # Read content asynchronously
    if hasattr(file, "read"):
        if asyncio.iscoroutinefunction(file.read):
            content = await file.read()
        else:
            content = await read_file_async(file)
    else:
        raise AttachmentValidationError("Invalid file object", filename=actual_filename)

    size_bytes = len(content)

    # Validate content type
    validate_content_type(actual_content_type, actual_filename)

    # Validate size
    validate_file_size(size_bytes, actual_filename)

    # Encode to base64 asynchronously
    content_b64 = await encode_base64_async(content)

    logger.debug(
        f"Processed attachment: {actual_filename}",
        extra={
            "extra_fields": {
                "filename": actual_filename,
                "content_type": actual_content_type,
                "size_bytes": size_bytes,
            }
        },
    )

    return ProcessedAttachment(
        filename=actual_filename,
        content_type=actual_content_type,
        content_base64=content_b64,
        size_bytes=size_bytes,
    )


async def validate_and_process_attachments(
    files: list,
    existing_attachments: list | None = None,
) -> tuple[list[ProcessedAttachment], int]:
    """Validate and process multiple uploaded files.

    Args:
        files: List of uploaded files
        existing_attachments: Existing attachments from JSON body (already validated)

    Returns:
        Tuple of (processed attachments, total size in bytes)

    Raises:
        AttachmentValidationError: If any validation fails
    """
    existing_count = len(existing_attachments) if existing_attachments else 0
    total_count = existing_count + len(files)

    # Validate count
    validate_attachments_count(total_count)

    # Process files concurrently
    tasks = [process_uploaded_file(f) for f in files]
    processed = await asyncio.gather(*tasks)

    # Calculate total size (including existing)
    total_size = sum(p.size_bytes for p in processed)

    # Add size of existing attachments (estimate from base64)
    if existing_attachments:
        for att in existing_attachments:
            if hasattr(att, "content_base64") and att.content_base64:
                # Base64 is ~4/3 the size of original
                total_size += int(len(att.content_base64) * 3 / 4)

    # Validate total size
    validate_total_size(total_size)

    return list(processed), total_size
