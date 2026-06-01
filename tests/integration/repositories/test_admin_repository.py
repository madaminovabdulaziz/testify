"""Integration test for ``AdminRepository``."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.user_repository import UserRepository


async def test_admin_repository_happy_path(session: AsyncSession) -> None:
    admin_repo = AdminRepository(session)
    user_repo = UserRepository(session)

    # ---------- create (seed owner, no parent admin) ----------
    owner = await admin_repo.create(
        telegram_id=900,
        role="owner",
        added_by_admin_id=None,
    )
    assert owner.id is not None
    assert owner.role == "owner"
    assert owner.user_id is None
    assert owner.added_by_admin_id is None

    # ---------- create (moderator added by owner) ----------
    mod = await admin_repo.create(
        telegram_id=901,
        role="moderator",
        added_by_admin_id=owner.id,
    )
    assert mod.added_by_admin_id == owner.id

    # ---------- get_by_id / get_by_telegram_id ----------
    by_id = await admin_repo.get_by_id(owner.id)
    assert by_id is not None and by_id.telegram_id == 900

    by_tg = await admin_repo.get_by_telegram_id(901)
    assert by_tg is not None and by_tg.id == mod.id

    assert await admin_repo.get_by_telegram_id(123) is None

    # ---------- list_all ordered by added_at ----------
    listing = await admin_repo.list_all()
    assert [a.telegram_id for a in listing] == [900, 901]

    # ---------- attach_user_id (admin starts the bot, gets a users row) ----------
    user = await user_repo.create(telegram_id=900, username="owner")
    await admin_repo.attach_user_id(owner.id, user.id)

    session.expunge_all()
    refreshed = await admin_repo.get_by_id(owner.id)
    assert refreshed is not None and refreshed.user_id == user.id
