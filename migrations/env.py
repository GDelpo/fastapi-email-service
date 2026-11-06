import asyncio
import os
from json import loads
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

target_metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

from app.models import (  # noqa: E402, F401
    Email,
    EmailAttachment,
    EmailBatch,
    EmailEvent,
    EmailSuppression,
    EmailTemplate,
)

exclude_tables = loads(os.getenv("DB_EXCLUDE_TABLES", "[]"))


def filter_db_objects(
    object,  # noqa: ARG001
    name,
    type_,
    *args,  # noqa: ARG001
    **kwargs,  # noqa: ARG001
):
    if type_ == "table":
        return name not in exclude_tables

    if type_ == "index" and name.startswith("idx") and name.endswith("geom"):
        return False

    return True


def run_migrations_offline():
    url = os.getenv("DATABASE_URL")
    if not url:
        # Construct URL from individual components if DATABASE_URL not set
        db_user = os.getenv("DB_USER", "email_service_user")
        db_password = os.getenv("DB_PASSWORD", "email_service_password")
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "email_service_db")
        url = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=filter_db_objects,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=filter_db_objects
        )
        context.run_migrations()


async def run_migrations_online():
    config_section = config.get_section(config.config_ini_section)
    url = os.getenv("DATABASE_URL")
    if not url:
        # Construct URL from individual components if DATABASE_URL not set
        db_user = os.getenv("DB_USER", "email_service_user")
        db_password = os.getenv("DB_PASSWORD", "email_service_password")
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "email_service_db")
        url = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    config_section["sqlalchemy.url"] = url

    connectable = AsyncEngine(
        engine_from_config(
            config_section,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
            future=True,
        )
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
