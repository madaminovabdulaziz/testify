"""Data access for the ``admins`` table.

Looked up on every admin-group / admin-DM update to authorize the actor
(``AdminOnly`` filter in the bot layer, Prompt 5).
"""

from __future__ import annotations

from sqlalchemy import select, update

from app.models.admin import Admin
from app.repositories.base import BaseRepository


class AdminRepository(BaseRepository):
    """Reads + writes for ``admins``."""

    async def get_by_id(self, admin_id: int) -> Admin | None:
        """Fetch one admin row by surrogate id, refreshed from the DB.

        ``populate_existing=True`` so a re-read after ``attach_user_id`` reflects
        the new ``user_id`` rather than a stale identity-map copy (see
        AttemptRepository.get_by_id for the full why).
        """
        return await self._session.get(Admin, admin_id, populate_existing=True)

    async def get_by_telegram_id(self, telegram_id: int) -> Admin | None:
        """Fetch the admin row matching a Telegram id (or ``None``)."""
        stmt = select(Admin).where(Admin.telegram_id == telegram_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[Admin]:
        """Return every admin row, ordered by ``added_at`` ascending."""
        stmt = select(Admin).order_by(Admin.added_at.asc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(
        self,
        telegram_id: int,
        role: str,
        added_by_admin_id: int | None,
    ) -> Admin:
        """Insert a new admin row and return it."""
        admin = Admin(
            telegram_id=telegram_id,
            role=role,
            added_by_admin_id=added_by_admin_id,
        )
        self._session.add(admin)
        await self._session.flush()
        return admin

    async def attach_user_id(self, admin_id: int, user_id: int) -> None:
        """Link an existing admin row to a ``users.id`` once that admin starts the bot."""
        stmt = update(Admin).where(Admin.id == admin_id).values(user_id=user_id)
        await self._session.execute(stmt)
