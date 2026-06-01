"""Unit tests for the receipt-photo handler.

Focus is the new defensive branches added for CODE_REVIEW C6 (per-user
submit lock), M3 (media-group dedup) and H8 (size / download / decode
guards). The service and container are mocked so we exercise only the
handler's branching.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramAPIError

from app.bot.handlers.payment import on_receipt_photo
from app.exceptions import ReceiptLimitExceededError


def _user(status: str = "pending_approval") -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        telegram_id=12345,
        status=status,
        full_name="Малика",
        phone="998901234567",
        username="malika",
        reference_code="A7F2K9",
    )


def _photo(*, file_size: int | None = 1000) -> SimpleNamespace:
    return SimpleNamespace(file_id="f1", file_unique_id="u1", file_size=file_size)


def _message(*, media_group_id: str | None = None, file_size: int | None = 1000) -> MagicMock:
    msg = MagicMock()
    msg.media_group_id = media_group_id
    msg.photo = [_photo(file_size=file_size)]
    msg.answer = AsyncMock()
    return msg


def _container(
    *,
    set_returns: object = True,
    submit_result: object | None = None,
    submit_raises: Exception | None = None,
    download_raises: Exception | None = None,
) -> MagicMock:
    services = MagicMock()
    if submit_raises is not None:
        services.receipt.submit = AsyncMock(side_effect=submit_raises)
    else:
        services.receipt.submit = AsyncMock(
            return_value=submit_result
            or SimpleNamespace(
                receipt=SimpleNamespace(id=55, created_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC)),
                warnings=(),
            )
        )
    services.receipt.attach_admin_notification_message = AsyncMock()
    services.settings.get = AsyncMock(return_value="✅ принято")

    container = MagicMock()
    container.services = MagicMock(return_value=services)
    container.settings = SimpleNamespace(env="dev", admin_group_id=-1001)

    container.redis = MagicMock()
    container.redis.set = AsyncMock(return_value=set_returns)
    container.redis.delete = AsyncMock(return_value=1)

    container.bot = MagicMock()
    if download_raises is not None:
        container.bot.download = AsyncMock(side_effect=download_raises)
    else:
        container.bot.download = AsyncMock()
    container.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=2))
    return container


def _state() -> MagicMock:
    return MagicMock(clear=AsyncMock(), set_state=AsyncMock())


async def _run(message, container, *, user=None, session=None, state=None) -> None:
    await on_receipt_photo(
        message,
        state=state or _state(),
        session=session or MagicMock(),
        user=user or _user(),
        container=container,
    )


# ---------- happy path ----------


async def test_happy_path_submits_and_releases_lock() -> None:
    message = _message()
    container = _container()

    await _run(message, container)

    container.services.return_value.receipt.submit.assert_awaited_once()
    container.bot.send_photo.assert_awaited_once()
    message.answer.assert_awaited()  # accepted message
    # Lock taken (nx) then released.
    container.redis.set.assert_awaited()
    container.redis.delete.assert_awaited_once()


# ---------- C6: per-user submit lock ----------


async def test_lock_already_held_tells_user_to_wait_and_skips_submit() -> None:
    message = _message()
    container = _container(set_returns=None)  # nx SET returns None == not acquired

    await _run(message, container)

    container.services.return_value.receipt.submit.assert_not_awaited()
    # Did not delete a lock it never held.
    container.redis.delete.assert_not_awaited()
    args = message.answer.await_args.args
    assert "обрабат" in args[0].lower()


# ---------- M3: media-group dedup ----------


async def test_media_group_followup_photo_is_silently_skipped() -> None:
    message = _message(media_group_id="mg42")
    # SETNX on the media-group marker returns None → not the first photo.
    container = _container(set_returns=None)

    await _run(message, container)

    # The media-group SETNX was attempted; submit never ran; no user reply.
    container.services.return_value.receipt.submit.assert_not_awaited()
    message.answer.assert_not_awaited()


# ---------- H8: size / download / decode guards ----------


async def test_oversized_photo_rejected_before_download() -> None:
    message = _message(file_size=6 * 1024 * 1024)
    container = _container()

    await _run(message, container)

    container.bot.download.assert_not_awaited()
    container.services.return_value.receipt.submit.assert_not_awaited()
    assert "МБ" in message.answer.await_args.args[0]


async def test_download_failure_surfaces_friendly_message() -> None:
    message = _message()
    container = _container(download_raises=TelegramAPIError(method=MagicMock(), message="boom"))

    await _run(message, container)

    container.services.return_value.receipt.submit.assert_not_awaited()
    assert "скачать" in message.answer.await_args.args[0].lower()


async def test_undecodable_image_surfaces_friendly_message_and_releases_lock() -> None:
    message = _message()
    container = _container(submit_raises=ValueError("not an image"))

    await _run(message, container)

    assert "обработать" in message.answer.await_args.args[0].lower()
    # Lock was acquired for the submit attempt, so it must be released.
    container.redis.delete.assert_awaited_once()


# ---------- service-side cap still surfaces ----------


async def test_pending_limit_surfaces_user_message_and_releases_lock() -> None:
    message = _message()
    container = _container(submit_raises=ReceiptLimitExceededError())

    await _run(message, container)

    assert message.answer.await_args.args[0] == ReceiptLimitExceededError.user_message
    container.redis.delete.assert_awaited_once()
