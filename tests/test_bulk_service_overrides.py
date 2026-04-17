"""Tests: create_bulk_email uses per-recipient html/subject overrides and returns queued_details."""
import datetime
import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.schemas.bulk import BulkEmailCreate, BulkRecipient

# ---------------------------------------------------------------------------
# Stub out aiosmtplib so the SMTP provider can be imported without the package
# ---------------------------------------------------------------------------
if "aiosmtplib" not in sys.modules:
    _stub = types.ModuleType("aiosmtplib")
    _stub.SMTP = MagicMock  # type: ignore[attr-defined]
    sys.modules["aiosmtplib"] = _stub


@pytest.mark.asyncio
async def test_per_recipient_html_used_over_batch_template():
    """When recipient has html_content, it overrides the batch-level template."""
    from app.service import EmailService

    repo = AsyncMock()
    batch_repo = AsyncMock()
    suppression_repo = AsyncMock()
    suppression_repo.check_multiple = AsyncMock(return_value={})

    created_email = MagicMock()
    created_email.id = uuid4()
    repo.create = AsyncMock(return_value=created_email)

    batch = MagicMock()
    batch.id = uuid4()
    batch.created_at = datetime.datetime.utcnow()
    batch_repo.create = AsyncMock(return_value=batch)
    batch_repo.update_status = AsyncMock()

    svc = EmailService(
        repo=repo,
        provider=MagicMock(),
        batch_repo=batch_repo,
        suppression_repo=suppression_repo,
        cache=None,
        template_repo=None,
    )

    bulk_data = BulkEmailCreate(
        subject="Fallback subject",
        html_content="<p>fallback</p>",
        recipients=[
            BulkRecipient(
                email="a@b.com",
                name="Alice",
                html_content="<p>per-recipient</p>",
                subject="Per-recipient subject",
                external_id="item-id-001",
            )
        ],
    )

    with patch("app.service.enqueue_send_email", new_callable=AsyncMock):
        result = await svc.create_bulk_email(bulk_data, owner_id=None, source_service="test")

    # Check the email was created with per-recipient content
    call_args = repo.create.call_args
    email_data = call_args.args[0] if call_args.args else call_args.kwargs["email_data"]
    assert email_data.html_content == "<p>per-recipient</p>"
    assert email_data.subject == "Per-recipient subject"

    # queued_details populated
    assert len(result.queued_details) == 1
    assert result.queued_details[0].external_id == "item-id-001"
    assert result.queued_details[0].message_id == str(created_email.id)
    assert result.queued_details[0].email == "a@b.com"


@pytest.mark.asyncio
async def test_fallback_to_batch_template_when_no_per_recipient_html():
    """When recipient has no html_content, batch-level template is used."""
    from app.service import EmailService

    repo = AsyncMock()
    batch_repo = AsyncMock()
    suppression_repo = AsyncMock()
    suppression_repo.check_multiple = AsyncMock(return_value={})

    created_email = MagicMock()
    created_email.id = uuid4()
    repo.create = AsyncMock(return_value=created_email)

    batch = MagicMock()
    batch.id = uuid4()
    batch.created_at = datetime.datetime.utcnow()
    batch_repo.create = AsyncMock(return_value=batch)
    batch_repo.update_status = AsyncMock()

    svc = EmailService(
        repo=repo,
        provider=MagicMock(),
        batch_repo=batch_repo,
        suppression_repo=suppression_repo,
        cache=None,
        template_repo=None,
    )

    bulk_data = BulkEmailCreate(
        subject="Batch subject",
        html_content="<p>batch html</p>",
        recipients=[
            BulkRecipient(email="a@b.com", name="Alice")  # no per-recipient html
        ],
    )

    with patch("app.service.enqueue_send_email", new_callable=AsyncMock):
        result = await svc.create_bulk_email(bulk_data, owner_id=None, source_service="test")

    call_args = repo.create.call_args
    email_data = call_args.args[0] if call_args.args else call_args.kwargs["email_data"]
    assert email_data.html_content == "<p>batch html</p>"
    assert email_data.subject == "Batch subject"

    assert len(result.queued_details) == 1
    assert result.queued_details[0].external_id is None


@pytest.mark.asyncio
async def test_suppressed_recipient_carries_external_id():
    """Skipped (suppressed) recipient echoes external_id in skipped_details."""
    from app.service import EmailService

    suppressed_mock = MagicMock()
    suppressed_mock.reason = MagicMock()
    suppressed_mock.reason.value = "bounce"
    suppressed_mock.description = "Hard bounce"

    repo = AsyncMock()
    batch_repo = AsyncMock()
    batch = MagicMock()
    batch.id = uuid4()
    batch.created_at = datetime.datetime.utcnow()
    batch_repo.create = AsyncMock(return_value=batch)
    batch_repo.update_status = AsyncMock()
    suppression_repo = AsyncMock()
    suppression_repo.check_multiple = AsyncMock(return_value={"a@b.com": suppressed_mock})

    svc = EmailService(
        repo=repo,
        provider=MagicMock(),
        batch_repo=batch_repo,
        suppression_repo=suppression_repo,
        cache=None,
        template_repo=None,
    )

    bulk_data = BulkEmailCreate(
        subject="S",
        html_content="<p>H</p>",
        recipients=[BulkRecipient(email="a@b.com", external_id="item-id-001")],
        skip_suppressed=True,
    )

    result = await svc.create_bulk_email(bulk_data)
    assert result.skipped == 1
    assert result.skipped_details[0].external_id == "item-id-001"
    assert result.queued_details == []
    repo.create.assert_not_called()
