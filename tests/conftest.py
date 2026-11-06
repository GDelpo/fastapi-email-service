"""Pytest fixtures and configuration."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from app.database import get_session
from app.main import app

# Use SQLite for testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def async_engine():
    """Create async engine for testing."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        future=True,
    )

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_session(async_engine) -> AsyncGenerator[AsyncSession]:
    """Create async session for testing."""
    async_session_maker = sessionmaker(
        async_engine,
        class_=SQLModelAsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(async_session) -> AsyncGenerator[AsyncClient]:
    """Create test client with overridden dependencies."""

    async def override_get_session():
        yield async_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def sample_email_data() -> dict[str, Any]:
    """Sample email creation data."""
    return {
        "to": [{"email": "test@example.com", "name": "Test User"}],
        "subject": "Test Email",
        "html_content": "<h1>Hello</h1><p>This is a test.</p>",
        "text_content": "Hello, this is a test.",
        "priority": "normal",
        "email_type": "transactional",
    }


@pytest.fixture
def sample_template_data() -> dict[str, Any]:
    """Sample template creation data."""
    return {
        "name": "Welcome Email",
        "slug": "welcome-email",
        "description": "Welcome email for new users",
        "subject_template": "Welcome, {{ name }}!",
        "html_template": "<h1>Welcome, {{ name }}!</h1>",
        "text_template": "Welcome, {{ name }}!",
        "variables": ["name"],
        "email_type": "transactional",
    }
