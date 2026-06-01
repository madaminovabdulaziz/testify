"""Unit tests for the admin approve/reject callback handlers.

We mock the bot, the services bundle, and the FSMContext so the tests
exercise just the handler's branching logic without any aiogram or DB
setup.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.callbacks.receipt import ReceiptDecisionCD
from app.bot.handlers.admin.receipts import (
    on_approve,
    on_reject_init,
    on_reject_reason,
)
from app.exceptions import ReceiptAlreadyProcessedError, ReceiptUserBannedError


def _admin_user() -> SimpleNamespace:
    return SimpleNamespace(id=99)


def _callback(*, telegram_id: int = 900, username: str = "admin1") -> MagicMock:
    cb = MagicMock()
    cb.from_user = SimpleNamespace(id=telegram_id, username=username)
    cb.message = MagicMock()
    cb.message.message_id = 1234
    cb.message.edit_caption = AsyncMock()
    cb.message.reply = AsyncMock()
    cb.answer = AsyncMock()
    return cb


_UNSET = object()


def _container_with_services(
    *,
    approve_returns=None,
    approve_raises=None,
    reject_returns=None,
    reject_raises=None,
    admin_returns: object = _UNSET,
    settings_values: dict[str, str] | None = None,
    dm_delivered: bool = True,
) -> MagicMock:
    settings_values = settings_values or {}
    resolved_admin = SimpleNamespace(id=1) if admin_returns is _UNSET else admin_returns

    services = MagicMock()
    services.admin.get_by_telegram_id = AsyncMock(return_value=resolved_admin)
    services.notification.send_user_message = AsyncMock(return_value=dm_delivered)

    if approve_raises:
        services.receipt.approve = AsyncMock(side_effect=approve_raises)
    else:
        services.receipt.approve = AsyncMock(return_value=approve_returns)

    if reject_raises:
        services.receipt.reject = AsyncMock(side_effect=reject_raises)
    else:
        services.receipt.reject = AsyncMock(return_value=reject_returns)

    async def fake_get(key: str) -> str | None:
        return settings_values.get(key)

    services.settings.get = AsyncMock(side_effect=fake_get)

    container = MagicMock()
    container.services = MagicMock(return_value=services)
    container.bot = MagicMock()
    container.bot.send_message = AsyncMock()
    container.bot.edit_message_caption = AsyncMock()
    container.settings.admin_group_id = -1001
    return container


# ---------- approve ----------


async def test_approve_happy_path_dms_user_and_edits_caption() -> None:
    callback = _callback()
    approved_user = SimpleNamespace(id=7, telegram_id=12345)
    container = _container_with_services(
        approve_returns=approved_user,
        settings_values={
            "group_invite_link": "https://t.me/+abc",
            "msg_approved": "OK {group_invite_link}",
        },
    )
    session = MagicMock()

    await on_approve(
        callback,
        callback_data=ReceiptDecisionCD(receipt_id=42, decision="approve"),
        session=session,
        user=_admin_user(),
        container=container,
    )

    container.services.return_value.receipt.approve.assert_awaited_once()
    callback.message.edit_caption.assert_awaited_once()
    # The approval DM goes through NotificationService (M7) and attaches the
    # main-menu reply keyboard so the student gets tappable actions.
    send = container.services.return_value.notification.send_user_message
    send.assert_awaited_once()
    call = send.await_args
    assert call.args == (7, 12345, "OK https://t.me/+abc")
    assert call.kwargs.get("reply_markup") is not None
    # The callback is now acked early (no text) so the button stops
    # spinning during the work below (CODE_REVIEW H7).
    callback.answer.assert_awaited_once_with()


async def test_approve_warns_admin_when_dm_blocked() -> None:
    # L5: student blocked the bot before approval — admin must learn the
    # invite link wasn't delivered.
    callback = _callback()
    approved_user = SimpleNamespace(id=7, telegram_id=12345)
    container = _container_with_services(
        approve_returns=approved_user,
        settings_values={"msg_approved": "OK {group_invite_link}"},
        dm_delivered=False,
    )

    await on_approve(
        callback,
        callback_data=ReceiptDecisionCD(receipt_id=42, decision="approve"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    callback.message.reply.assert_awaited_once()
    assert "заблокировал" in callback.message.reply.await_args.args[0].lower()


async def test_approve_handles_already_processed() -> None:
    callback = _callback()
    container = _container_with_services(approve_raises=ReceiptAlreadyProcessedError())
    session = MagicMock()

    await on_approve(
        callback,
        callback_data=ReceiptDecisionCD(receipt_id=42, decision="approve"),
        session=session,
        user=_admin_user(),
        container=container,
    )

    # Early ack already spent answer(); the double-tap notice now comes as a
    # group reply (CODE_REVIEW H7).
    callback.answer.assert_awaited_once_with()
    callback.message.reply.assert_awaited_once_with("Этот чек уже обработан.")
    container.bot.send_message.assert_not_awaited()


async def test_approve_banned_user_warns_admin_and_skips_dm() -> None:
    callback = _callback()
    container = _container_with_services(approve_raises=ReceiptUserBannedError())
    session = MagicMock()

    await on_approve(
        callback,
        callback_data=ReceiptDecisionCD(receipt_id=42, decision="approve"),
        session=session,
        user=_admin_user(),
        container=container,
    )

    # Banned user: admin is warned in-group, student is NOT DMed, caption
    # is left intact (CODE_REVIEW C2).
    callback.message.reply.assert_awaited_once()
    assert "заблокирован" in callback.message.reply.await_args.args[0].lower()
    container.bot.send_message.assert_not_awaited()
    callback.message.edit_caption.assert_not_awaited()


async def test_approve_blocks_non_admin_telegram_id() -> None:
    callback = _callback()
    container = _container_with_services(admin_returns=None)
    session = MagicMock()

    await on_approve(
        callback,
        callback_data=ReceiptDecisionCD(receipt_id=42, decision="approve"),
        session=session,
        user=_admin_user(),
        container=container,
    )

    container.services.return_value.receipt.approve.assert_not_awaited()


# ---------- reject init ----------


async def test_reject_init_sets_fsm_state_and_prompts_for_reason() -> None:
    callback = _callback()
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    await on_reject_init(
        callback,
        callback_data=ReceiptDecisionCD(receipt_id=42, decision="reject"),
        state=state,
    )

    callback.message.reply.assert_awaited()
    state.set_state.assert_awaited_once()
    state.update_data.assert_awaited_once_with(receipt_id=42, admin_message_id=1234)


# ---------- reject reason ----------


def _message(*, text: str = "размытое фото") -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=900, username="admin1")
    msg.reply = AsyncMock()
    return msg


@pytest.mark.parametrize("cancel_text", ["отмена", "ОТМЕНА", "  отмена  "])
async def test_reject_reason_cancellation_clears_state(cancel_text: str) -> None:
    message = _message(text=cancel_text)
    state = MagicMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value={"receipt_id": 42, "admin_message_id": 1})
    container = _container_with_services()
    session = MagicMock()

    await on_reject_reason(
        message,
        state=state,
        session=session,
        user=_admin_user(),
        container=container,
    )

    state.clear.assert_awaited_once()
    container.services.return_value.receipt.reject.assert_not_awaited()


async def test_reject_reason_happy_path() -> None:
    message = _message(text="нечитаемое")
    state = MagicMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value={"receipt_id": 42, "admin_message_id": 1})
    rejected_user = SimpleNamespace(id=5, telegram_id=12345)
    container = _container_with_services(
        reject_returns=rejected_user,
        settings_values={"msg_rejected": "Reason: {reason}"},
    )
    session = MagicMock()

    await on_reject_reason(
        message,
        state=state,
        session=session,
        user=_admin_user(),
        container=container,
    )

    container.services.return_value.receipt.reject.assert_awaited_once()
    container.bot.edit_message_caption.assert_awaited_once()
    # Rejection DM goes through NotificationService now (M7).
    container.services.return_value.notification.send_user_message.assert_awaited_once_with(
        5, 12345, "Reason: нечитаемое"
    )
    state.clear.assert_awaited_once()
    message.reply.assert_awaited()


async def test_reject_reason_already_processed_clears_state() -> None:
    message = _message()
    state = MagicMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value={"receipt_id": 42, "admin_message_id": 1})
    container = _container_with_services(reject_raises=ReceiptAlreadyProcessedError())
    session = MagicMock()

    await on_reject_reason(
        message,
        state=state,
        session=session,
        user=_admin_user(),
        container=container,
    )

    state.clear.assert_awaited_once()
    message.reply.assert_awaited()
