"""SQLAlchemy async engine + sessionmaker factory.

The engine is long-lived (one per process); sessions are short-lived
(one per Telegram update, created by ``DbSessionMiddleware`` in Prompt 4).
``expire_on_commit=False`` is essential for async because attribute
access after a commit would otherwise trigger lazy I/O outside the
session context.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_engine_and_session(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build the async engine and a matching session factory.

    The engine is configured with the pool settings from ``Settings`` and
    ``pool_pre_ping=True`` so a stale connection (the proxy/server reset
    it between updates) is quietly recycled rather than raising.
    """
    engine = create_async_engine(
        settings.db_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max_overflow,
        pool_pre_ping=True,
        future=True,
    )
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    return engine, session_factory
