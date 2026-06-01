"""Integration test for ``UserRepository``."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.user_repository import UserRepository


async def _fresh(session: AsyncSession, user_id: int) -> User:
    """Re-fetch ``users.id`` bypassing the identity map (post-bulk-UPDATE reads)."""
    session.expunge_all()
    user = await session.get(User, user_id)
    assert user is not None
    return user


async def test_user_repository_happy_path(session: AsyncSession) -> None:
    repo = UserRepository(session)

    # ---------- create ----------
    user = await repo.create(telegram_id=100, username="alice")
    assert user.id is not None
    assert user.status == "new"
    assert user.bot_blocked is False

    # ---------- get_by_id / get_by_telegram_id ----------
    by_id = await repo.get_by_id(user.id)
    assert by_id is not None and by_id.telegram_id == 100

    by_tg = await repo.get_by_telegram_id(100)
    assert by_tg is not None and by_tg.id == user.id

    assert await repo.get_by_telegram_id(99999) is None

    # ---------- setters ----------
    # Phones are stored in normalized digits-only form (the service layer runs
    # normalize_phone before persisting); find_by_query relies on that. Store
    # the normalized value here so the repo test mirrors production.
    await repo.set_phone(user.id, "998901234567")
    await repo.set_name(user.id, "Alice Smith")
    await repo.set_reference_code(user.id, "A7F2K9")
    await repo.set_status(user.id, "pending_approval")
    refreshed = await _fresh(session, user.id)
    assert refreshed.phone == "998901234567"
    assert refreshed.full_name == "Alice Smith"
    assert refreshed.reference_code == "A7F2K9"
    assert refreshed.status == "pending_approval"

    # ---------- mark_approved (sets status + approved_at, idempotent) ----------
    await repo.mark_approved(user.id)
    approved = await _fresh(session, user.id)
    assert approved.status == "approved"
    assert approved.approved_at is not None
    first_approved_at = approved.approved_at

    await repo.mark_approved(user.id)  # idempotent
    re_approved = await _fresh(session, user.id)
    assert re_approved.approved_at == first_approved_at  # COALESCE preserved original

    # ---------- mark_bot_blocked ----------
    await repo.mark_bot_blocked(user.id)
    blocked = await _fresh(session, user.id)
    assert blocked.bot_blocked is True

    # ---------- find_by_query (phone / username / ref_code) ----------
    # restore bot_blocked = False so the broadcast assertion later is meaningful
    blocked.bot_blocked = False
    await session.flush()

    found_phone = await repo.find_by_query("+998901234567")
    found_username = await repo.find_by_query("@alice")  # leading @ tolerated
    found_code = await repo.find_by_query("#a7f2k9")  # # and lowercase tolerated
    assert found_phone is not None and found_phone.id == user.id
    assert found_username is not None and found_username.id == user.id
    assert found_code is not None and found_code.id == user.id
    assert await repo.find_by_query("nope") is None

    # ---------- list_approved_for_broadcast ----------
    # Add a second approved user + one blocked user; only the first two should appear.
    other = await repo.create(telegram_id=101, username="bob")
    await repo.mark_approved(other.id)

    blocked_user = await repo.create(telegram_id=102, username="carol")
    await repo.mark_approved(blocked_user.id)
    await repo.mark_bot_blocked(blocked_user.id)

    recipients = await repo.list_approved_for_broadcast()
    tg_ids = {tg for (_id, tg) in recipients}
    assert tg_ids == {100, 101}
