"""Tests for BulkEmailCreate per-recipient content validation."""

import pytest
from pydantic import ValidationError

from app.schemas.bulk import BulkEmailCreate, BulkRecipient, QueuedRecipient, SkippedRecipient


def test_batch_level_content_accepted():
    data = BulkEmailCreate(
        subject="Subject",
        html_content="<p>body</p>",
        recipients=[BulkRecipient(email="a@b.com")],
    )
    assert data.subject == "Subject"


def test_per_recipient_content_accepted_without_batch_level():
    data = BulkEmailCreate(
        recipients=[
            BulkRecipient(email="a@b.com", html_content="<p>A</p>", subject="Sub A"),
            BulkRecipient(email="b@b.com", html_content="<p>B</p>", subject="Sub B"),
        ],
    )
    assert data.recipients[0].html_content == "<p>A</p>"


def test_missing_content_raises():
    with pytest.raises(ValidationError, match="Must provide"):
        BulkEmailCreate(
            recipients=[BulkRecipient(email="a@b.com")],
        )


def test_partial_per_recipient_raises():
    """Some recipients have per-recipient content, some don't — must fail."""
    with pytest.raises(ValidationError, match="Must provide"):
        BulkEmailCreate(
            recipients=[
                BulkRecipient(email="a@b.com", html_content="<p>A</p>", subject="Sub A"),
                BulkRecipient(email="b@b.com"),  # missing html_content + subject
            ],
        )


def test_external_id_roundtrip():
    data = BulkEmailCreate(
        subject="S",
        html_content="<p>H</p>",
        recipients=[BulkRecipient(email="x@y.com", external_id="item-uuid-123")],
    )
    assert data.recipients[0].external_id == "item-uuid-123"


def test_queued_recipient_schema():
    qr = QueuedRecipient(email="a@b.com", message_id="msg-1", external_id="ext-1")
    assert qr.message_id == "msg-1"
    assert qr.external_id == "ext-1"


def test_skipped_recipient_has_external_id():
    sr = SkippedRecipient(email="a@b.com", reason="suppressed", external_id="ext-1")
    assert sr.external_id == "ext-1"


def test_queued_recipient_camel_output():
    qr = QueuedRecipient(email="a@b.com", message_id="msg-1", external_id="ext-1")
    data = qr.model_dump(by_alias=True)
    assert "messageId" in data
    assert "externalId" in data
    assert "message_id" not in data
