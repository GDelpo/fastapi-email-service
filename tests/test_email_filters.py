# mailsender/tests/test_email_filters.py
"""Tests for new email repository filters: to_email, date_from, date_to."""

# NOTE: These tests run on SQLite which does not support true ILIKE.
# SQLAlchemy translates ilike() to LIKE on SQLite, which only handles ASCII
# case-insensitivity. Production uses PostgreSQL where ILIKE works for all
# Unicode characters. Non-ASCII to_email searches are not validated here.

from datetime import UTC, datetime, timedelta

import pytest

from app.repository.email import EmailRepository
from app.schemas import EmailCreate, EmailRecipient


def _make_email(to: str, subject: str = "Test") -> EmailCreate:
    return EmailCreate(
        to=[EmailRecipient(email=to)],
        subject=subject,
        html_content="<p>test</p>",
    )


@pytest.mark.asyncio
async def test_filter_by_to_email_matches(async_session):
    """to_email filter returns only emails whose to_emails contains the string."""
    repo = EmailRepository(session=async_session)
    await repo.create(_make_email("juan@example.com"))
    await repo.create(_make_email("maria@example.com"))

    results = await repo.get_all(to_email="juan")
    assert len(results) == 1
    assert "juan" in results[0].to_emails


@pytest.mark.asyncio
async def test_filter_by_to_email_no_match(async_session):
    """to_email filter with non-matching string returns empty list."""
    repo = EmailRepository(session=async_session)
    await repo.create(_make_email("test@example.com"))

    results = await repo.get_all(to_email="nomatch@nowhere.com")
    assert results == []


@pytest.mark.asyncio
async def test_filter_by_date_from(async_session):
    """date_from filter excludes emails created before the date."""
    repo = EmailRepository(session=async_session)
    await repo.create(_make_email("test@example.com"))

    now = datetime.now(UTC)
    tomorrow = now + timedelta(days=1)

    results = await repo.get_all(date_from=tomorrow)
    assert results == []


@pytest.mark.asyncio
async def test_filter_by_date_to(async_session):
    """date_to filter excludes emails created after the date."""
    repo = EmailRepository(session=async_session)
    await repo.create(_make_email("test@example.com"))

    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)

    results = await repo.get_all(date_to=yesterday)
    assert results == []


@pytest.mark.asyncio
async def test_filter_by_date_range_includes(async_session):
    """date_from + date_to range that covers now returns the email."""
    repo = EmailRepository(session=async_session)
    await repo.create(_make_email("test@example.com"))

    now = datetime.now(UTC)
    results = await repo.get_all(
        date_from=now - timedelta(days=1),
        date_to=now + timedelta(days=1),
    )
    assert len(results) == 1


@pytest.mark.asyncio
async def test_count_respects_to_email_filter(async_session):
    """count() is consistent with get_all() for to_email filter."""
    repo = EmailRepository(session=async_session)
    await repo.create(_make_email("alpha@example.com"))
    await repo.create(_make_email("beta@example.com"))

    count = await repo.count(to_email="alpha")
    assert count == 1
