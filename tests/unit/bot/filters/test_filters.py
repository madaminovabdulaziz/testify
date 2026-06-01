"""Unit tests for the four bot filters."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.filters.admin_group_only import AdminGroupOnly
from app.bot.filters.admin_only import AdminOnly
from app.bot.filters.approved_only import ApprovedOnly
from app.bot.filters.photo_only import PhotoOnly


def _msg_with_sender(*, telegram_id: int = 1, chat_id: int = 42) -> MagicMock:
    """Build an aiogram-Message-shaped mock with from_user + chat."""
    msg = MagicMock()
    msg.from_user = SimpleNamespace(id=telegram_id, username="x")
    msg.chat = SimpleNamespace(id=chat_id)
    return msg


# ---------- AdminOnly ----------


async def test_admin_only_returns_true_for_admin_telegram_id() -> None:
    filt = AdminOnly()
    session = MagicMock()  # placeholder; we monkeypatch the repo below
    msg = _msg_with_sender(telegram_id=900)

    # Patch the AdminRepository import the filter uses.
    import app.bot.filters.admin_only as mod

    fake_repo = MagicMock()
    fake_repo.get_by_telegram_id = AsyncMock(return_value=SimpleNamespace(id=1))
    mod.AdminRepository = MagicMock(return_value=fake_repo)

    assert await filt(msg, session=session) is True
    fake_repo.get_by_telegram_id.assert_awaited_once_with(900)


async def test_admin_only_returns_false_for_unknown_telegram_id() -> None:
    filt = AdminOnly()
    session = MagicMock()
    msg = _msg_with_sender(telegram_id=12345)

    import app.bot.filters.admin_only as mod

    fake_repo = MagicMock()
    fake_repo.get_by_telegram_id = AsyncMock(return_value=None)
    mod.AdminRepository = MagicMock(return_value=fake_repo)

    assert await filt(msg, session=session) is False


async def test_admin_only_fails_closed_when_session_is_missing() -> None:
    """No session in data dict → not-admin (safer than blowing up)."""
    filt = AdminOnly()
    msg = _msg_with_sender()

    assert await filt(msg) is False


async def test_admin_only_returns_false_for_anonymous_event() -> None:
    """Channel posts have no from_user; the filter must not crash."""
    filt = AdminOnly()
    msg = MagicMock()
    msg.from_user = None

    assert await filt(msg, session=MagicMock()) is False


# ---------- ApprovedOnly ----------


async def test_approved_only_true_when_user_is_approved() -> None:
    filt = ApprovedOnly()
    user = SimpleNamespace(status="approved")
    assert await filt(MagicMock(), user=user) is True


async def test_approved_only_false_when_user_is_pending() -> None:
    filt = ApprovedOnly()
    user = SimpleNamespace(status="pending_payment")
    assert await filt(MagicMock(), user=user) is False


async def test_approved_only_false_when_no_user_loaded() -> None:
    filt = ApprovedOnly()
    assert await filt(MagicMock()) is False


# ---------- AdminGroupOnly ----------


async def test_admin_group_only_true_for_matching_chat() -> None:
    filt = AdminGroupOnly(admin_group_id=-1001)

    msg = MagicMock()
    msg.chat = SimpleNamespace(id=-1001)

    assert await filt(msg) is True


async def test_admin_group_only_false_for_other_chat() -> None:
    filt = AdminGroupOnly(admin_group_id=-1001)

    msg = MagicMock()
    msg.chat = SimpleNamespace(id=-9999)

    assert await filt(msg) is False


# ---------- PhotoOnly ----------


async def test_photo_only_true_when_photo_attached() -> None:
    filt = PhotoOnly()
    msg = MagicMock()
    msg.photo = [SimpleNamespace(file_id="x")]
    assert await filt(msg) is True


async def test_photo_only_false_when_no_photo() -> None:
    filt = PhotoOnly()
    msg = MagicMock()
    msg.photo = None
    assert await filt(msg) is False
