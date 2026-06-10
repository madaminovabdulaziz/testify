"""Data access for the ``broadcasts`` table."""

from __future__ import annotations

from sqlalchemy import select, update

from app.models.broadcast import Broadcast
from app.repositories.base import BaseRepository
from app.utils.datetime import now_utc


class BroadcastRepository(BaseRepository):
    """Reads + writes for ``broadcasts``."""

    async def create(
        self,
        *,
        source_chat_id: int,
        source_message_id: int,
        created_by_admin_id: int | None,
        report_chat_id: int | None,
        total_recipients: int,
    ) -> Broadcast:
        """Insert a fresh ``in_progress`` broadcast row and return it."""
        broadcast = Broadcast(
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            created_by_admin_id=created_by_admin_id,
            report_chat_id=report_chat_id,
            total_recipients=total_recipients,
            status="in_progress",
        )
        self._session.add(broadcast)
        await self._session.flush()
        return broadcast

    async def get_by_id(self, broadcast_id: int) -> Broadcast | None:
        """Fetch one broadcast, refreshed from the DB (resume reads need fresh counts)."""
        return await self._session.get(Broadcast, broadcast_id, populate_existing=True)

    async def list_in_progress(self) -> list[Broadcast]:
        """Unfinished broadcasts — scanned at startup to resume delivery."""
        stmt = select(Broadcast).where(Broadcast.status == "in_progress").order_by(Broadcast.id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def record_progress(
        self,
        broadcast_id: int,
        *,
        sent_count: int,
        blocked_count: int,
        error_count: int,
        last_user_id: int,
    ) -> None:
        """Persist absolute progress counters + the resume cursor."""
        stmt = (
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                sent_count=sent_count,
                blocked_count=blocked_count,
                error_count=error_count,
                last_user_id=last_user_id,
            )
        )
        await self._session.execute(stmt)

    async def mark_completed(self, broadcast_id: int) -> int:
        """Finish a broadcast. Status-guarded so a double-finish is a no-op."""
        stmt = (
            update(Broadcast)
            .where(Broadcast.id == broadcast_id, Broadcast.status == "in_progress")
            .values(status="completed", finished_at=now_utc())
        )
        result = await self._session.execute(stmt)
        return self._rowcount(result)
