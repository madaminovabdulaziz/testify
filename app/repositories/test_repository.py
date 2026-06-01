"""Data access for the ``tests`` table.

The "exactly one active test" invariant is enforced by the service
layer's ``TestService.publish`` running ``mark_archived`` + ``mark_active``
inside one transaction (DATABASE_SPEC §6.2). This repo just provides
the moves.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update

from app.models.test import Test
from app.repositories.base import BaseRepository
from app.utils.datetime import now_utc


class TestRepository(BaseRepository):
    """Reads + writes for ``tests``."""

    # Tell pytest this isn't a unittest-style test class. Without this,
    # the ``Test*`` python_classes pattern matches whenever a test module
    # imports ``TestRepository`` into its namespace.
    __test__ = False

    async def get_by_id(self, test_id: int) -> Test | None:
        """Fetch one test by id, refreshed from the DB.

        ``populate_existing=True`` so a re-read right after ``publish``→
        ``mark_active`` reflects the new ``published_at`` instead of the stale
        identity-map value (see AttemptRepository.get_by_id for the full why).
        """
        return await self._session.get(Test, test_id, populate_existing=True)

    async def get_active(self) -> Test | None:
        """Return the currently active test, or ``None`` if no test is published."""
        # populate_existing: a publish() in the same session mutates the row via
        # a Core UPDATE; force the ORM to reflect the DB rather than a cached copy.
        stmt = (
            select(Test)
            .where(Test.status == "active")
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_draft(
        self,
        *,
        title: str,
        duration_seconds: int,
        created_by_admin_id: int | None,
    ) -> Test:
        """Insert a new ``draft`` test row and return it."""
        test = Test(
            title=title,
            duration_seconds=duration_seconds,
            created_by_admin_id=created_by_admin_id,
            status="draft",
        )
        self._session.add(test)
        await self._session.flush()
        return test

    async def mark_active(self, test_id: int) -> int:
        """Promote a ``draft`` test to ``active`` and stamp ``published_at``. Returns rowcount."""
        stmt = (
            update(Test)
            .where(Test.id == test_id, Test.status == "draft")
            .values(status="active", published_at=now_utc())
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def mark_archived(self, test_id: int) -> int:
        """Move an ``active`` test to ``archived`` and stamp ``archived_at``. Returns rowcount."""
        stmt = (
            update(Test)
            .where(Test.id == test_id, Test.status == "active")
            .values(status="archived", archived_at=now_utc())
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def delete_draft(self, test_id: int) -> int:
        """Hard-delete a ``draft`` test (questions cascade). Returns rowcount."""
        stmt = delete(Test).where(Test.id == test_id, Test.status == "draft")
        result = await self._session.execute(stmt)
        return result.rowcount

    async def count_by_status(self) -> dict[str, int]:
        """``{status: count}`` across all tests — feeds /stats."""
        stmt = select(Test.status, func.count()).group_by(Test.status)
        rows = await self._session.execute(stmt)
        return {status: int(count) for status, count in rows}
