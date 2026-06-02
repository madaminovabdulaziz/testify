"""Alembic migration environment.

Reads the database URL from environment variables (never from alembic.ini)
and runs migrations against an async engine, so the same SQLAlchemy stack
the application uses is exercised here too.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Application metadata for `--autogenerate`. Importing ``app.models`` (the
# package) runs its ``__init__.py``, which in turn imports every model module
# and registers each table on ``Base.metadata``. Without that side-effect,
# autogenerate would only see the tables whose modules happen to have been
# imported elsewhere.
from app.models import Base

target_metadata = Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _build_url() -> str:
    """Compose an async MySQL URL from environment variables.

    ``DB_HOST``/``DB_USER``/``DB_PASSWORD``/``DB_NAME`` are required and have no
    default — the same contract as ``app.core.config.Settings``. Falling back to
    ``localhost`` here used to mask a missing-config (e.g. unset Railway
    variables) as a confusing connection-refused traceback; fail loudly instead.
    Locally these come from ``.env`` (the Makefile sources it before alembic).
    """
    missing = [k for k in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            "Missing required DB env var(s): "
            + ", ".join(missing)
            + ". On Railway set the DB_* reference variables (docs/RAILWAY.md §3); "
            "locally source .env first."
        )
    host = os.environ["DB_HOST"]
    port = os.environ.get("DB_PORT", "3306")
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    name = os.environ["DB_NAME"]
    return f"mysql+asyncmy://{user}:{password}@{host}:{port}/{name}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode, emitting SQL to stdout."""
    context.configure(
        url=_build_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Build an async engine and apply migrations through a sync-bridged connection."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _build_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
