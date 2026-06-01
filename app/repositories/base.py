"""Base class for every repository.

Holds the per-request ``AsyncSession`` and nothing else. No generic
``get/list/create/update/delete`` helpers — every repository spells out
exactly the methods it offers so the service layer's data-access surface
is grep-able and explicit (ARCHITECTURE_SPEC §4.1).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Stores the session injected by ``DbSessionMiddleware``."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
