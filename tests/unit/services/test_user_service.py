"""Unit tests for :class:`app.services.user_service.UserService`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.exceptions import InvalidNameError
from app.services.user_service import UserService


def _fake_user(
    *, id: int = 1, status: str = "new", approved_at: object | None = None
) -> SimpleNamespace:
    """Lightweight stand-in for the SQLAlchemy ``User`` model."""
    return SimpleNamespace(id=id, status=status, approved_at=approved_at)


# ---------- get_or_create ----------


async def test_get_or_create_returns_existing_user() -> None:
    existing = _fake_user(id=7, status="approved")
    repo = AsyncMock()
    repo.get_by_telegram_id = AsyncMock(return_value=existing)
    repo.create = AsyncMock()

    svc = UserService(repo)
    result = await svc.get_or_create(telegram_id=100, username="alice")

    assert result is existing
    repo.create.assert_not_awaited()


async def test_get_or_create_creates_when_missing() -> None:
    created = _fake_user(id=10, status="new")
    repo = AsyncMock()
    repo.get_by_telegram_id = AsyncMock(return_value=None)
    repo.create = AsyncMock(return_value=created)

    svc = UserService(repo)
    result = await svc.get_or_create(telegram_id=100, username="alice")

    assert result is created
    repo.create.assert_awaited_once_with(telegram_id=100, username="alice")


# ---------- find ----------


async def test_find_delegates_to_repository() -> None:
    target = _fake_user(id=3)
    repo = AsyncMock()
    repo.find_by_query = AsyncMock(return_value=target)

    svc = UserService(repo)
    assert await svc.find("+998901234567") is target
    repo.find_by_query.assert_awaited_once_with("+998901234567")


# ---------- set_phone ----------


async def test_set_phone_persists_and_advances_state() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status="onboarding_phone"))
    repo.set_phone = AsyncMock()
    repo.set_status = AsyncMock()

    svc = UserService(repo)
    await svc.set_phone(1, "+998901234567")

    # Stored in canonical digits-only form so /find matches later (H18).
    repo.set_phone.assert_awaited_once_with(1, "998901234567")
    repo.set_status.assert_awaited_once_with(1, "onboarding_name")


async def test_set_phone_skips_write_on_unexpected_status() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status="approved"))
    repo.set_phone = AsyncMock()
    repo.set_status = AsyncMock()

    svc = UserService(repo)
    await svc.set_phone(1, "+998901234567")

    # A settled (e.g. approved) user must not have their phone or status
    # rewritten by a stale contact-share (CODE_REVIEW H6/H13).
    repo.set_phone.assert_not_awaited()
    repo.set_status.assert_not_awaited()


async def test_set_phone_missing_user_is_a_noop() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.set_phone = AsyncMock()

    svc = UserService(repo)
    await svc.set_phone(999, "+998")

    repo.set_phone.assert_not_awaited()


# ---------- set_name ----------


async def test_set_name_persists_after_validation_and_strips() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status="onboarding_name"))
    repo.set_name = AsyncMock()
    svc = UserService(repo)

    await svc.set_name(1, "  Alice Smith  ")

    repo.set_name.assert_awaited_once_with(1, "Alice Smith")


async def test_set_name_skips_write_on_unexpected_status() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status="approved"))
    repo.set_name = AsyncMock()
    svc = UserService(repo)

    # Valid name, but the user is past the name-capture step — don't
    # overwrite a settled user's name (CODE_REVIEW H6/H13).
    await svc.set_name(1, "Alice Smith")

    repo.set_name.assert_not_awaited()


@pytest.mark.parametrize(
    "bad",
    [
        "",  # empty
        "A",  # too short
        "x" * 81,  # too long
        "12345",  # no letters
        "  !!!  ",  # only symbols, also too short after strip
        "A" + "😀" * 79,  # one letter buried in emoji (M2)
        "Alice‮melА",  # RTL override injection (M2)
        "Bob​Smith",  # zero-width space (M2)
    ],
)
async def test_set_name_rejects_invalid_input(bad: str) -> None:
    repo = AsyncMock()
    repo.set_name = AsyncMock()
    svc = UserService(repo)

    with pytest.raises(InvalidNameError):
        await svc.set_name(1, bad)

    repo.set_name.assert_not_awaited()


# ---------- attach_reference_code ----------


async def test_attach_reference_code_persists_and_advances_to_pending_payment() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status="onboarding_name"))
    repo.set_reference_code = AsyncMock()
    repo.set_status = AsyncMock()

    svc = UserService(repo)
    await svc.attach_reference_code(1, "A7F2K9")

    repo.set_reference_code.assert_awaited_once_with(1, "A7F2K9")
    repo.set_status.assert_awaited_once_with(1, "pending_payment")


async def test_attach_reference_code_skips_write_on_unexpected_status() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status="approved"))
    repo.set_reference_code = AsyncMock()
    repo.set_status = AsyncMock()

    svc = UserService(repo)
    await svc.attach_reference_code(1, "CODE12")

    # An approved user keeps their original reference_code — it's the
    # admin's code↔deposit link (CODE_REVIEW H6). Nothing is written.
    repo.set_reference_code.assert_not_awaited()
    repo.set_status.assert_not_awaited()


# ---------- mark_pending_approval ----------


@pytest.mark.parametrize("source_status", ["pending_payment", "rejected"])
async def test_mark_pending_approval_advances_from_valid_source(source_status: str) -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status=source_status))
    repo.set_status = AsyncMock()

    svc = UserService(repo)
    await svc.mark_pending_approval(1)

    repo.set_status.assert_awaited_once_with(1, "pending_approval")


async def test_mark_pending_approval_noop_on_already_approved() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status="approved"))
    repo.set_status = AsyncMock()

    svc = UserService(repo)
    await svc.mark_pending_approval(1)

    repo.set_status.assert_not_awaited()


# ---------- mark_approved / mark_rejected ----------


async def test_mark_approved_delegates_to_repo() -> None:
    repo = AsyncMock()
    repo.mark_approved = AsyncMock(return_value=1)
    svc = UserService(repo)

    await svc.mark_approved(1)
    repo.mark_approved.assert_awaited_once_with(1)


async def test_mark_rejected_delegates_to_guarded_repo() -> None:
    repo = AsyncMock()
    repo.mark_rejected = AsyncMock(return_value=1)
    svc = UserService(repo)

    await svc.mark_rejected(1)
    # Uses the status-guarded repo method (not the generic set_status) so a
    # banned user's receipt rejection can't un-ban them (CODE_REVIEW C2).
    repo.mark_rejected.assert_awaited_once_with(1)


async def test_mark_rejected_noops_when_repo_reports_zero_rows() -> None:
    repo = AsyncMock()
    repo.mark_rejected = AsyncMock(return_value=0)  # banned/missing user
    svc = UserService(repo)

    await svc.mark_rejected(1)  # must not raise; just logs
    repo.mark_rejected.assert_awaited_once_with(1)


# ---------- ban / unban ----------


async def test_ban_sets_banned_status() -> None:
    repo = AsyncMock()
    repo.set_status = AsyncMock()
    svc = UserService(repo)

    await svc.ban(1)
    repo.set_status.assert_awaited_once_with(1, "banned")


async def test_unban_restores_to_approved_when_previously_approved() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(
        return_value=_fake_user(id=1, status="banned", approved_at="2026-05-01T00:00:00Z")
    )
    repo.set_status = AsyncMock()
    svc = UserService(repo)

    assert await svc.unban(1) is True
    repo.set_status.assert_awaited_once_with(1, "approved")


async def test_unban_refuses_when_never_approved() -> None:
    # CODE_REVIEW M16: a user banned from a pre-approval state has no
    # approved_at — unban must not grant them access.
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status="banned", approved_at=None))
    repo.set_status = AsyncMock()
    svc = UserService(repo)

    assert await svc.unban(1) is False
    repo.set_status.assert_not_awaited()


async def test_unban_noop_when_not_banned() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_fake_user(id=1, status="approved"))
    repo.set_status = AsyncMock()
    svc = UserService(repo)

    await svc.unban(1)
    repo.set_status.assert_not_awaited()


# ---------- mark_bot_blocked ----------


async def test_mark_bot_blocked_delegates_to_repo() -> None:
    repo = AsyncMock()
    repo.mark_bot_blocked = AsyncMock()
    svc = UserService(repo)

    await svc.mark_bot_blocked(1)
    repo.mark_bot_blocked.assert_awaited_once_with(1)
