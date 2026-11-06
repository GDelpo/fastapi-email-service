"""Webhook verification and bulk sender checks. No provider, Redis or network needed."""

import base64
import json
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient

from app.config import settings
from app.exceptions import ValidationError
from app.schemas.bulk import BulkEmailCreate, BulkRecipient
from app.webhooks import verify_mailchimp_secret, verify_sendgrid_signature

EVENTS = json.dumps([{"event": "delivered", "email": "test@example.com"}]).encode()
TIMESTAMP = "1767225600"


@pytest.fixture
def sendgrid_keys(monkeypatch):
    """Generate a P-256 key pair; configure its public half like SendGrid's verification key."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setattr(settings, "sendgrid_webhook_key", base64.b64encode(public_der).decode())
    monkeypatch.setattr(settings, "debug", False)

    def sign(payload: bytes, timestamp: str = TIMESTAMP) -> str:
        der = private_key.sign(timestamp.encode() + payload, ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(der).decode()

    return sign


def test_sendgrid_valid_ecdsa_signature(sendgrid_keys):
    assert verify_sendgrid_signature(EVENTS, sendgrid_keys(EVENTS), TIMESTAMP) is True


@pytest.mark.parametrize(
    "tamper",
    [
        lambda sig: (EVENTS + b" ", sig, TIMESTAMP),  # body changed
        lambda sig: (EVENTS, sig, "1767225601"),  # timestamp changed
        lambda sig: (EVENTS, base64.b64encode(b"not-a-signature").decode(), TIMESTAMP),
        lambda sig: (EVENTS, None, TIMESTAMP),
        lambda sig: (EVENTS, sig, None),
    ],
)
def test_sendgrid_rejects_bad_signatures(sendgrid_keys, tamper):
    assert verify_sendgrid_signature(*tamper(sendgrid_keys(EVENTS))) is False


def test_sendgrid_hmac_style_signature_is_rejected(sendgrid_keys):
    """The old HMAC-SHA256 hex digest is not what SendGrid sends."""
    import hashlib
    import hmac

    fake = hmac.new(b"key", TIMESTAMP.encode() + EVENTS, hashlib.sha256).hexdigest()
    assert verify_sendgrid_signature(EVENTS, fake, TIMESTAMP) is False


@pytest.mark.parametrize("debug", [False, True])
def test_missing_keys_reject_outside_debug(monkeypatch, debug):
    monkeypatch.setattr(settings, "debug", debug)
    monkeypatch.setattr(settings, "sendgrid_webhook_key", None)
    monkeypatch.setattr(settings, "mailchimp_webhook_key", None)

    assert verify_sendgrid_signature(EVENTS, None, None) is debug
    assert verify_mailchimp_secret(None) is debug


def test_mailchimp_shared_secret(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "mailchimp_webhook_key", "s3cret-webhook-key")

    assert verify_mailchimp_secret("s3cret-webhook-key") is True
    assert verify_mailchimp_secret("wrong") is False
    assert verify_mailchimp_secret(None) is False


@pytest.mark.asyncio
async def test_signed_sendgrid_webhook_end_to_end(client: AsyncClient, sendgrid_keys):
    response = await client.post(
        "/api/v1/webhooks/sendgrid",
        content=EVENTS,
        headers={
            "Content-Type": "application/json",
            "X-Twilio-Email-Event-Webhook-Signature": sendgrid_keys(EVENTS),
            "X-Twilio-Email-Event-Webhook-Timestamp": TIMESTAMP,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"received": 1}


@pytest.mark.asyncio
async def test_unsigned_webhooks_are_rejected_in_production(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "sendgrid_webhook_key", None)
    monkeypatch.setattr(settings, "mailchimp_webhook_key", None)

    sendgrid = await client.post("/api/v1/webhooks/sendgrid", content=EVENTS)
    mailchimp = await client.post("/api/v1/webhooks/mailchimp", data={"type": "unsubscribe"})
    assert sendgrid.status_code == 401
    assert mailchimp.status_code == 401


@pytest.mark.asyncio
async def test_bulk_without_any_sender_is_refused_before_creating_a_batch(monkeypatch):
    from app.service import EmailService

    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "mail_sender_email", "")
    batch_repo = AsyncMock()
    service = EmailService(
        repo=AsyncMock(),
        provider=AsyncMock(),
        batch_repo=batch_repo,
        suppression_repo=None,
        cache=None,
        template_repo=None,
    )
    bulk = BulkEmailCreate(
        subject="S", html_content="<p>H</p>", recipients=[BulkRecipient(email="a@example.com")]
    )

    with pytest.raises(ValidationError):
        await service.create_bulk_email(bulk)
    batch_repo.create.assert_not_called()
