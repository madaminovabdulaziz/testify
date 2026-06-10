"""Per-request DB session scope for web handlers.

Separate from ``app.web.setup`` so handler modules can import it without
creating a setup ↔ handlers import cycle.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container


@asynccontextmanager
async def session_scope(container: Container) -> AsyncIterator[AsyncSession]:
    """One DB session per web request: commit on success, rollback on error.

    Mirrors the bot's ``DbSessionMiddleware`` semantics so the service layer
    behaves identically regardless of which front door called it.
    """
    async with container.session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
