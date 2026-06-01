"""Integration test for ``TestRepository``."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.test_repository import TestRepository


async def test_test_repository_happy_path(session: AsyncSession) -> None:
    admins = AdminRepository(session)
    tests = TestRepository(session)

    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)

    # ---------- nothing active yet ----------
    assert await tests.get_active() is None

    # ---------- create_draft ----------
    draft1 = await tests.create_draft(
        title="Тест от 2026-05-21",
        duration_seconds=3200,
        created_by_admin_id=admin.id,
    )
    assert draft1.status == "draft"
    assert draft1.duration_seconds == 3200

    # ---------- get_by_id ----------
    fetched = await tests.get_by_id(draft1.id)
    assert fetched is not None and fetched.id == draft1.id
    assert await tests.get_by_id(9999) is None

    # ---------- mark_active ----------
    rowcount = await tests.mark_active(draft1.id)
    assert rowcount == 1
    # second call is a no-op because draft1 is no longer 'draft'
    assert await tests.mark_active(draft1.id) == 0
    session.expunge_all()
    active = await tests.get_active()
    assert active is not None and active.id == draft1.id
    assert active.published_at is not None

    # ---------- mark_archived ----------
    assert await tests.mark_archived(draft1.id) == 1
    session.expunge_all()
    assert await tests.get_active() is None
    archived = await tests.get_by_id(draft1.id)
    assert archived is not None
    assert archived.status == "archived"
    assert archived.archived_at is not None

    # ---------- delete_draft ----------
    draft2 = await tests.create_draft(
        title="Throwaway draft",
        duration_seconds=3200,
        created_by_admin_id=admin.id,
    )
    rowcount = await tests.delete_draft(draft2.id)
    assert rowcount == 1
    assert await tests.get_by_id(draft2.id) is None
    # Already-archived row cannot be deleted via this method.
    assert await tests.delete_draft(draft1.id) == 0
