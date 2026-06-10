"""Unit tests for the admin announcement compose → preview → confirm flow."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.callbacks.broadcast import BroadcastConfirmCD
from app.bot.handlers.admin.broadcast import (
    broadcast_cancel,
    cmd_broadcast,
    on_announcement_message,
    on_confirm_send,
)
from app.bot.states.admin import AdminBroadcastState


def _container(*, recipients: int = 5) -> MagicMock:
    container = MagicMock()
    services = MagicMock()
    services.broadcast.count_recipients = AsyncMock(return_value=recipients)
    services.broadcast.create = AsyncMock(
        return_value=SimpleNamespace(id=3, total_recipients=recipients)
    )
    services.admin.get_by_telegram_id = AsyncMock(return_value=SimpleNamespace(id=7))
    container.services = MagicMock(return_value=services)
    container.bot.copy_message = AsyncMock()
    return container


def _message(*, content_type: str = "text", media_group_id: str | None = None) -> MagicMock:
    msg = MagicMock()
    msg.content_type = content_type
    msg.media_group_id = media_group_id
    msg.chat = SimpleNamespace(id=555, type="private")
    msg.message_id = 777
    msg.answer = AsyncMock()
    return msg


def _state(*, current: str | None = None, data: dict | None = None) -> MagicMock:
    state = MagicMock()
    state.set_state = AsyncMock()
    state.get_state = AsyncMock(return_value=current)
    state.get_data = AsyncMock(return_value=data or {})
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    return state


async def test_cmd_broadcast_opens_compose_state() -> None:
    msg = _message()
    state = _state()

    await cmd_broadcast(msg, state=state)

    state.set_state.assert_awaited_once_with(AdminBroadcastState.waiting_for_message)
    assert "Рассылка" in msg.answer.await_args.args[0]


async def test_album_is_rejected() -> None:
    msg = _message(content_type="photo", media_group_id="album-1")
    state = _state()
    container = _container()

    await on_announcement_message(
        msg, state=state, session=MagicMock(), user=MagicMock(), container=container
    )

    assert "Альбомы" in msg.answer.await_args.args[0]
    state.set_state.assert_not_awaited()


async def test_unsupported_content_rejected() -> None:
    msg = _message(content_type="poll")
    state = _state()
    container = _container()

    await on_announcement_message(
        msg, state=state, session=MagicMock(), user=MagicMock(), container=container
    )

    assert "нельзя разослать" in msg.answer.await_args.args[0]


async def test_no_recipients_aborts_flow() -> None:
    msg = _message()
    state = _state()
    container = _container(recipients=0)

    await on_announcement_message(
        msg, state=state, session=MagicMock(), user=MagicMock(), container=container
    )

    state.clear.assert_awaited_once()
    assert "рассылать некому" in msg.answer.await_args.args[0]


async def test_valid_message_previews_and_asks_confirmation() -> None:
    msg = _message(content_type="photo")
    state = _state()
    container = _container(recipients=12)

    await on_announcement_message(
        msg, state=state, session=MagicMock(), user=MagicMock(), container=container
    )

    # Preview = copy back to the same chat.
    container.bot.copy_message.assert_awaited_once_with(
        chat_id=555, from_chat_id=555, message_id=777
    )
    state.set_state.assert_awaited_once_with(AdminBroadcastState.confirming)
    state.update_data.assert_awaited_once_with(source_chat_id=555, source_message_id=777)
    confirm = msg.answer.await_args
    assert "Отправить" in confirm.args[0]
    keyboard = confirm.kwargs["reply_markup"]
    assert "12" in keyboard.inline_keyboard[0][0].text


async def test_confirm_send_commits_before_spawn_and_reports() -> None:
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=111)
    callback.message = MagicMock()
    callback.message.chat = SimpleNamespace(id=555)
    callback.message.edit_text = AsyncMock()
    state = _state(
        current=AdminBroadcastState.confirming.state,
        data={"source_chat_id": 555, "source_message_id": 777},
    )
    container = _container()
    session = MagicMock()
    session.commit = AsyncMock()

    order: list[str] = []
    session.commit.side_effect = lambda: order.append("commit")

    with patch("app.bot.handlers.admin.broadcast.spawn_broadcast") as spawn:
        spawn.side_effect = lambda c, bid: order.append("spawn")
        await on_confirm_send(
            callback,
            state=state,
            session=session,
            user=MagicMock(),
            container=container,
        )

    # The durable row must be visible to the runner's own session first.
    assert order == ["commit", "spawn"]
    spawn.assert_called_once_with(container, 3)
    state.clear.assert_awaited_once()
    assert "Рассылка #3 запущена" in callback.message.edit_text.await_args.args[0]


async def test_confirm_send_double_tap_is_noop() -> None:
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=111)
    callback.message = MagicMock()
    callback.message.edit_reply_markup = AsyncMock()
    state = _state(current=None)  # already cleared by the first tap
    container = _container()

    with patch("app.bot.handlers.admin.broadcast.spawn_broadcast") as spawn:
        await on_confirm_send(
            callback,
            state=state,
            session=MagicMock(),
            user=MagicMock(),
            container=container,
        )

    spawn.assert_not_called()
    container.services.return_value.broadcast.create.assert_not_awaited()


async def test_cancel_returns_to_panel() -> None:
    msg = _message()
    state = _state()

    await broadcast_cancel(msg, state=state)

    state.clear.assert_awaited_once()
    assert "отменена" in msg.answer.await_args.args[0]


def test_confirm_callback_data_roundtrip() -> None:
    packed = BroadcastConfirmCD(action="send").pack()
    assert BroadcastConfirmCD.unpack(packed).action == "send"
