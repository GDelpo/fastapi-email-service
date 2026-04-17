"""End-to-end tests for Mail Sender API."""

import pytest
from httpx import AsyncClient


class TestHealthCheck:
    """Health check endpoint tests."""

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """Test health check returns 200."""
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["name"] == "Mail Sender API"


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
    async def test_sendgrid_webhook_invalid_json(self, client: AsyncClient):
        """Test SendGrid webhook rejects invalid JSON."""
        response = await client.post(
            "/api/v1/webhooks/sendgrid",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_sendgrid_webhook_accepts_events(self, client: AsyncClient):
        """Test SendGrid webhook accepts valid events."""
        events = [
            {
                "event": "delivered",
                "sg_message_id": "abc123.domain",
                "email": "test@example.com",
            }
        ]
        response = await client.post("/api/v1/webhooks/sendgrid", json=events)
        # Without webhook key configured, it should accept
        assert response.status_code == 200
        assert response.json()["received"] == 1
