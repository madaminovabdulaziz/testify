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
from app.core.config import build_async_mysql_url
from app.models import Base

target_metadata = Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _build_url() -> str:
    """Async MySQL URL from env — DATABASE_URL/MYSQL_URL or the discrete DB_* vars.

    Delegates to ``app.core.config.build_async_mysql_url`` so migrations use the
    exact same resolution (and error message) as the app and the seed scripts.
    Locally these come from ``.env`` (the Makefile sources it before alembic).
    """
    return build_async_mysql_url(os.environ)


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
