"""End-to-end tests for Email Service API."""

import pytest
from httpx import AsyncClient

from app.config import settings


class TestHealthCheck:
    """Health check endpoint tests."""

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """The root endpoint answers with the service name and its links."""
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "Email Service API"
        assert data["health"] == "/health"


class TestEmailEndpoints:
    """Email endpoint tests."""

    @pytest.mark.asyncio
    async def test_create_email_requires_auth(self, client: AsyncClient, sample_email_data):
        """Test that creating email requires authentication."""
        response = await client.post("/api/v1/emails", json=sample_email_data)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_emails_requires_auth(self, client: AsyncClient):
        """Test that listing emails requires authentication."""
        response = await client.get("/api/v1/emails")
        assert response.status_code == 401


class TestWebhooks:
    """Webhook endpoint tests."""

    @pytest.mark.asyncio
    async def test_sendgrid_webhook_invalid_json(self, client: AsyncClient, monkeypatch):
        """Test SendGrid webhook rejects invalid JSON (debug mode, no key)."""
        monkeypatch.setattr(settings, "debug", True)
        response = await client.post(
            "/api/v1/webhooks/sendgrid",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_sendgrid_webhook_accepts_events(self, client: AsyncClient, monkeypatch):
        """Without a webhook key, events are accepted only in debug mode."""
        monkeypatch.setattr(settings, "debug", True)
        events = [
            {
                "event": "delivered",
                "sg_message_id": "abc123.domain",
                "email": "test@example.com",
            }
        ]
        response = await client.post("/api/v1/webhooks/sendgrid", json=events)
        assert response.status_code == 200
        assert response.json()["received"] == 1
