"""Data access for the ``settings`` table.

The Redis caching layer lives in ``SettingsService`` (ARCHITECTURE_SPEC
§8.6); this module is the cache miss path.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.models.setting import Setting
from app.repositories.base import BaseRepository


class SettingsRepository(BaseRepository):
    """Reads + writes for ``settings``."""

    async def get(self, key: str) -> str | None:
        """Return the value for ``key``, or ``None`` if the row is missing."""
        stmt = select(Setting.value).where(Setting.key == key).limit(1)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def set(
        self,
        key: str,
        value: str,
        updated_by_admin_id: int | None,
    ) -> None:
        """Upsert one ``(key, value)`` pair, stamping ``updated_by_admin_id``."""
        stmt = mysql_insert(Setting).values(
            key=key,
            value=value,
            updated_by_admin_id=updated_by_admin_id,
        )
        stmt = stmt.on_duplicate_key_update(
            value=stmt.inserted.value,
            updated_by_admin_id=stmt.inserted.updated_by_admin_id,
        )
        await self._session.execute(stmt)

    async def get_all(self) -> dict[str, str]:
        """Snapshot every ``(key, value)`` pair."""
        stmt = select(Setting.key, Setting.value)
        rows = await self._session.execute(stmt)
        return {row.key: row.value for row in rows}
