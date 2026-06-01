"""Unit tests for admin test-upload + publish handlers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.callbacks.publish import PublishCD
from app.bot.handlers.admin import tests as admin_tests
from app.bot.handlers.admin.tests import (
    cmd_upload_test,
    on_collecting_non_photo,
    on_publish_cancel,
    on_publish_silent,
    on_question_image,
    wait_for_pending_broadcasts,
)
from app.bot.states.admin import AdminTestUploadState

# ---------- M13: broadcast drain on shutdown ----------


async def test_wait_for_pending_broadcasts_returns_when_none() -> None:
    admin_tests._BACKGROUND_TASKS.clear()
    await wait_for_pending_broadcasts(timeout=1.0)  # must not hang or raise


async def test_wait_for_pending_broadcasts_awaits_inflight_task() -> None:
    admin_tests._BACKGROUND_TASKS.clear()
    done = False

    async def _work() -> None:
        nonlocal done
        await asyncio.sleep(0.01)
        done = True

    task = asyncio.create_task(_work())
    admin_tests._BACKGROUND_TASKS.add(task)
    task.add_done_callback(admin_tests._BACKGROUND_TASKS.discard)

    await wait_for_pending_broadcasts(timeout=5.0)
    assert done is True


def _admin_user() -> SimpleNamespace:
    return SimpleNamespace(id=99, status="approved")


def _services(*, publish_returns=None, publish_raises=None) -> MagicMock:
    services = MagicMock()
    if publish_raises:
        services.test.publish = AsyncMock(side_effect=publish_raises)
    else:
        services.test.publish = AsyncMock(return_value=publish_returns)
    services.test.cancel_draft = AsyncMock()
    services.admin.get_by_telegram_id = AsyncMock(return_value=SimpleNamespace(id=1))
    return services


def _container(services: MagicMock) -> MagicMock:
    container = MagicMock()
    container.services = MagicMock(return_value=services)
    container.bot = MagicMock()
    container.bot.send_message = AsyncMock()
    container.settings.admin_group_id = -1001
    return container


def _callback() -> MagicMock:
    cb = MagicMock()
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.chat = SimpleNamespace(id=-1001)
    return cb


# ---------- /upload_test ----------


async def test_cmd_upload_test_sets_state_and_prompts() -> None:
    message = MagicMock()
    message.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()

    await cmd_upload_test(message, state=state)

    state.set_state.assert_awaited_once_with(AdminTestUploadState.waiting_for_file)
    message.answer.assert_awaited_once()


# ---------- cancel callback ----------


async def test_cancel_deletes_draft_and_clears_state() -> None:
    callback = _callback()
    state = MagicMock()
    state.clear = AsyncMock()
    services = _services()
    container = _container(services)

    await on_publish_cancel(
        callback,
        callback_data=PublishCD(draft_id=7, action="cancel"),
        state=state,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.test.cancel_draft.assert_awaited_once_with(7)
    state.clear.assert_awaited_once()
    callback.message.edit_text.assert_awaited()


# ---------- silent publish ----------


async def test_publish_silent_calls_service_without_broadcast() -> None:
    callback = _callback()
    state = MagicMock()
    state.clear = AsyncMock()
    services = _services(publish_returns=SimpleNamespace(id=7))
    container = _container(services)

    await on_publish_silent(
        callback,
        callback_data=PublishCD(draft_id=7, action="publish_silent"),
        state=state,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.test.publish.assert_awaited_once_with(7, notify=False)
    state.clear.assert_awaited_once()
    callback.message.edit_text.assert_awaited()


async def test_publish_silent_handles_publish_failure() -> None:
    callback = _callback()
    state = MagicMock()
    state.clear = AsyncMock()
    services = _services(publish_raises=ValueError("draft not in 'draft' status"))
    container = _container(services)

    await on_publish_silent(
        callback,
        callback_data=PublishCD(draft_id=7, action="publish_silent"),
        state=state,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    state.clear.assert_awaited_once()
    callback.message.edit_text.assert_awaited()


# ---------- question-image collection ----------


def _photo_message() -> MagicMock:
    msg = MagicMock()
    msg.photo = [SimpleNamespace(file_id="img-fid", file_unique_id="img-uid")]
    msg.answer = AsyncMock()
    return msg


def _image_services(*, pending_after_attach: list[int]) -> MagicMock:
    services = MagicMock()
    # First read returns the full pending list; the post-attach read returns
    # whatever the test wants to simulate (more pending, or empty → preview).
    services.test.pending_image_positions = AsyncMock(side_effect=[[3, 19], pending_after_attach])
    services.test.attach_question_image = AsyncMock(return_value=True)
    services.test.count_image_questions = AsyncMock(return_value=2)
    services.test.get_test = AsyncMock(return_value=SimpleNamespace(id=7, title="T"))
    return services


async def test_question_image_attaches_and_prompts_next() -> None:
    msg = _photo_message()
    state = MagicMock()
    state.get_data = AsyncMock(return_value={"draft_test_id": 7})
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    services = _image_services(pending_after_attach=[19])  # one still pending
    container = _container(services)

    await on_question_image(
        msg,
        state=state,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.test.attach_question_image.assert_awaited_once_with(
        7, 3, file_id="img-fid", file_unique_id="img-uid"
    )
    # Still collecting — prompt for the next one, don't show the preview yet.
    state.set_state.assert_not_awaited()
    msg.answer.assert_awaited_once()
    assert "19" in msg.answer.await_args.args[0]


async def test_question_image_last_one_shows_preview() -> None:
    msg = _photo_message()
    state = MagicMock()
    state.get_data = AsyncMock(return_value={"draft_test_id": 7})
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    services = _image_services(pending_after_attach=[])  # none left → preview
    container = _container(services)

    await on_question_image(
        msg,
        state=state,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.test.attach_question_image.assert_awaited_once()
    # Collection complete → move to confirming_publish and render the preview.
    state.set_state.assert_awaited_once_with(AdminTestUploadState.confirming_publish)
    assert any(
        "Загружен новый тест" in (c.args[0] if c.args else "") for c in msg.answer.await_args_list
    )


async def test_collecting_non_photo_reminds_to_send_image() -> None:
    msg = MagicMock()
    msg.answer = AsyncMock()
    await on_collecting_non_photo(msg)
    msg.answer.assert_awaited_once()
    assert "фото" in msg.answer.await_args.args[0].lower()
