"""Integration test for :class:`UserService` against real MySQL."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import InvalidNameError
from app.repositories.user_repository import UserRepository
from app.services.user_service import UserService


async def _fresh_status(session: AsyncSession, user_id: int) -> str:
    """Re-read the user's status bypassing the identity map."""
    repo = UserRepository(session)
    session.expunge_all()
    user = await repo.get_by_id(user_id)
    assert user is not None
    return user.status


async def test_user_service_onboarding_to_approval_flow(session: AsyncSession) -> None:
    repo = UserRepository(session)
    svc = UserService(repo)

    # ---------- get_or_create ----------
    user = await svc.get_or_create(telegram_id=500, username="alice")
    assert user.id is not None
    assert user.status == "new"

    # get_or_create returns the same row on the second call
    again = await svc.get_or_create(telegram_id=500, username="alice")
    assert again.id == user.id

    # ---------- onboarding: phone → name → ref code ----------
    # Simulate the welcome → "Начать" flow by walking from new through
    # the funnel via the service operations.
    await svc.set_phone(user.id, "+998901234567")
    assert await _fresh_status(session, user.id) == "onboarding_name"

    with pytest.raises(InvalidNameError):
        await svc.set_name(user.id, "")
    await svc.set_name(user.id, "  Alice Smith  ")

    await svc.attach_reference_code(user.id, "A7F2K9")
    assert await _fresh_status(session, user.id) == "pending_payment"

    # ---------- approval funnel ----------
    await svc.mark_pending_approval(user.id)
    assert await _fresh_status(session, user.id) == "pending_approval"

    await svc.mark_approved(user.id)
    assert await _fresh_status(session, user.id) == "approved"
    # Idempotent re-approval keeps approved_at stable.
    session.expunge_all()
    first = await repo.get_by_id(user.id)
    assert first is not None and first.approved_at is not None
    first_approved_at = first.approved_at
    await svc.mark_approved(user.id)
    session.expunge_all()
    second = await repo.get_by_id(user.id)
    assert second is not None and second.approved_at == first_approved_at

    # ---------- ban / unban ----------
    await svc.ban(user.id)
    assert await _fresh_status(session, user.id) == "banned"

    await svc.unban(user.id)
    assert await _fresh_status(session, user.id) == "approved"

    # ---------- find ----------
    found = await svc.find("+998901234567")
    assert found is not None and found.id == user.id

    # ---------- mark_bot_blocked ----------
    await svc.mark_bot_blocked(user.id)
    session.expunge_all()
    final = await repo.get_by_id(user.id)
    assert final is not None and final.bot_blocked is True
