"""Admin test-upload + publish handlers.

Implements PRODUCT_BLUEPRINT §8.4. Works in both the admin group and an
admin's DM with the bot (§14.3), so this router is **not** gated by
``F.chat.id == admin_group_id`` — each handler stacks ``AdminOnly()``
instead.

The notify-publish path spawns a fire-and-forget broadcast task so the
callback returns immediately; the task reports the per-user counts back
to the same chat when done. ``TestService.publish`` already does the
archive-of-old + activate-of-new atomically (Prompt 7).
"""

from __future__ import annotations

import asyncio
import contextlib
from io import BytesIO
from pathlib import Path

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.callbacks.publish import PublishCD
from app.bot.filters.admin_only import AdminOnly
from app.bot.states.admin import AdminTestUploadState
from app.bot.views import RenderedMessage
from app.bot.views.test_preview import (
    render_image_request,
    render_parse_errors,
    render_test_preview,
)
from app.core.container import Container, Services
from app.exceptions import TestParseError
from app.models.test import Test
from app.models.user import User

logger = structlog.get_logger()

# Hold strong references to fire-and-forget broadcast tasks so the GC
# doesn't pull them out from under asyncio (the loop only keeps weak
# refs). Discarded via add_done_callback once each task finishes.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()

# Bound on how long a graceful shutdown waits for an in-flight broadcast to
# drain (CODE_REVIEW M13). A new-test broadcast to ~1k students finishes well
# inside this; anything slower is abandoned so shutdown isn't held hostage.
_BROADCAST_DRAIN_TIMEOUT_SECONDS = 30.0


async def wait_for_pending_broadcasts(timeout: float = _BROADCAST_DRAIN_TIMEOUT_SECONDS) -> None:
    """Await any in-flight broadcast tasks on shutdown.

    A new-test broadcast runs as a fire-and-forget background task; without
    this, a graceful restart (SIGTERM) cancels it mid-fan-out and a chunk of
    students silently never get the notification (CODE_REVIEW M13 — the
    graceful slice of H14). Hard crashes still lose it; durable resume is a
    v1.1 concern per ARCHITECTURE_SPEC A4.
    """
    pending = [t for t in _BACKGROUND_TASKS if not t.done()]
    if not pending:
        return
    logger.info("awaiting_pending_broadcasts", count=len(pending))
    _, still_pending = await asyncio.wait(pending, timeout=timeout)
    if still_pending:
        logger.warning("broadcasts_unfinished_on_shutdown", count=len(still_pending))


router = Router(name="admin_tests")

# Every handler in this router requires admin status.
router.message.filter(AdminOnly())
router.callback_query.filter(AdminOnly())

_TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "static" / "template.xlsx"
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # PRODUCT_BLUEPRINT §13: 5 MB cap

_UPLOAD_PROMPT = "Отправьте файл Excel с тестом. Шаблон: /template"
_WRONG_EXTENSION = "Нужен файл .xlsx"
_FILE_TOO_LARGE = "Файл слишком большой. Максимум 5 МБ."
_FILE_SIZE_UNKNOWN = "Не удалось определить размер файла. Отправьте файл .xlsx ещё раз."
_FALLBACK_BROADCAST_TEXT = (
    "📢 Доступен новый тест!\n\n"
    "Откройте бота и нажмите «Пройти тест», чтобы начать.\n\n"
    "⏱ У вас будет 53 минуты 20 секунд."
)
_PARSE_CRASH_FALLBACK = "Не удалось прочитать файл. Проверьте формат."
_CANCELLED_REPLY = "🗑 Загрузка отменена."
_PUBLISHED_SILENT_REPLY = "✅ Тест опубликован (без рассылки)."
_PUBLISHED_NOTIFY_REPLY = (
    "✅ Тест опубликован. Рассылка студентам запущена; я сообщу здесь, когда она завершится."
)
_OUT_OF_FLOW_DOC_REPLY = "Чтобы загрузить тест, сначала используйте /upload_test."
_EXPECT_IMAGE = (
    "Пожалуйста, отправьте изображение (фото) для текущего вопроса или нажмите «🗑 Отменить»."
)
_IMAGE_FLOW_LOST = "Не удалось продолжить загрузку. Начните заново через /upload_test."


# ---------- /upload_test ----------


@router.message(Command("upload_test"))
async def cmd_upload_test(message: Message, state: FSMContext) -> None:
    """Kick off the upload flow — wait for the next document from this admin."""
    await state.set_state(AdminTestUploadState.waiting_for_file)
    await message.answer(_UPLOAD_PROMPT)


# ---------- /template ----------


@router.message(Command("template"))
async def cmd_template(message: Message) -> None:
    """Ship the bundled .xlsx template back to the requester."""
    if not _TEMPLATE_PATH.exists():
        # Should not happen — scripts/generate_template.py produces it in CI;
        # log loudly if we ever ship without it.
        logger.error("template_file_missing", path=str(_TEMPLATE_PATH))
        await message.answer("Шаблон временно недоступен. Сообщите администратору.")
        return
    await message.answer_document(
        document=FSInputFile(_TEMPLATE_PATH, filename="template.xlsx"),
        caption=(
            "Шаблон Excel. Заполните и отправьте через /upload_test.\n\n"
            "Чтобы прикрепить картинку (таблицу, схему) к вопросу, укажите «да» "
            "в колонке has_image — бот попросит фото после загрузки."
        ),
    )


# ---------- document received while in upload flow ----------


@router.message(
    StateFilter(AdminTestUploadState.waiting_for_file),
    F.document,
)
async def on_test_file(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Receive the uploaded .xlsx, parse + persist a draft, show the preview."""
    document = message.document
    assert document is not None  # F.document filter guarantees this

    file_name = (document.file_name or "").lower()
    if not file_name.endswith(".xlsx"):
        await message.answer(_WRONG_EXTENSION)
        return

    # Refuse before downloading into memory if the size is over the cap OR
    # unknown — a None size means we can't bound the download, and a
    # crafted .xlsx (zip-bomb) shouldn't be pulled in blind (CODE_REVIEW
    # H16). openpyxl already parses with read_only=True.
    if document.file_size is None:
        await message.answer(_FILE_SIZE_UNKNOWN)
        return
    if document.file_size > _MAX_UPLOAD_BYTES:
        await message.answer(_FILE_TOO_LARGE)
        return

    buf = BytesIO()
    await container.bot.download(document.file_id, destination=buf)
    file_bytes = buf.getvalue()

    services = container.services(session)
    admin = await services.admin.get_by_telegram_id(message.from_user.id)
    admin_id = admin.id if admin is not None else None

    try:
        draft = await services.test.create_draft_from_excel(
            file_bytes, uploaded_by_admin_id=admin_id
        )
    except TestParseError as exc:
        await message.answer(render_parse_errors(exc.errors))
        # Stay in waiting_for_file so the admin can resend a fixed file
        # without re-issuing /upload_test.
        return
    except Exception:
        logger.exception("test_upload_crash")
        await message.answer(_PARSE_CRASH_FALLBACK)
        return

    # If any question is flagged for an illustration, collect the photos in-bot
    # before showing the publish buttons (PRODUCT_BLUEPRINT §8.4, image
    # extension). Otherwise go straight to the preview as before.
    pending = await services.test.pending_image_positions(draft.id)
    if pending:
        await state.set_state(AdminTestUploadState.collecting_images)
        await state.update_data(draft_test_id=draft.id)
        await _send(message, render_image_request(draft.id, pending))
        return

    await _show_preview(message, state, test=draft, services=services)


# ---------- question-image collection (during upload) ----------


@router.message(StateFilter(AdminTestUploadState.collecting_images), F.photo)
async def on_question_image(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Assign the sent photo to the next question awaiting an illustration."""
    assert message.photo is not None  # F.photo guarantees this
    data = await state.get_data()
    draft_id = data.get("draft_test_id")
    if draft_id is None:
        await state.clear()
        await message.answer(_IMAGE_FLOW_LOST)
        return

    services = container.services(session)
    pending = await services.test.pending_image_positions(draft_id)
    if not pending:
        await _finish_image_collection(message, state, draft_id, services)
        return

    target = pending[0]
    photo = message.photo[-1]  # largest rendition
    await services.test.attach_question_image(
        draft_id,
        target,
        file_id=photo.file_id,
        file_unique_id=photo.file_unique_id,
    )
    logger.info("question_image_attached", test_id=draft_id, position=target)

    remaining = await services.test.pending_image_positions(draft_id)
    if remaining:
        await _send(message, render_image_request(draft_id, remaining, saved_position=target))
        return
    await _finish_image_collection(message, state, draft_id, services)


@router.message(StateFilter(AdminTestUploadState.collecting_images))
async def on_collecting_non_photo(message: Message) -> None:
    """Any non-photo input while collecting question images — gentle reminder."""
    await message.answer(_EXPECT_IMAGE)


# ---------- documents outside the upload flow ----------


@router.message(F.document)
async def on_document_outside_flow(message: Message) -> None:
    """An admin sent us a document without /upload_test first (§13)."""
    await message.answer(_OUT_OF_FLOW_DOC_REPLY)


# ---------- publish / cancel callbacks ----------


@router.callback_query(PublishCD.filter(F.action == "cancel"))
async def on_publish_cancel(
    callback: CallbackQuery,
    callback_data: PublishCD,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """🗑 Cancel — hard-delete the draft + clear FSM."""
    await callback.answer()
    services = container.services(session)
    await services.test.cancel_draft(callback_data.draft_id)
    await state.clear()
    if callback.message is not None:
        with contextlib.suppress(TelegramAPIError):
            await callback.message.edit_text(_CANCELLED_REPLY)


@router.callback_query(PublishCD.filter(F.action == "publish_silent"))
async def on_publish_silent(
    callback: CallbackQuery,
    callback_data: PublishCD,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """📤 Publish without broadcast."""
    await callback.answer()
    services = container.services(session)
    try:
        await services.test.publish(callback_data.draft_id, notify=False)
    except ValueError as exc:
        # Draft was cancelled / already published out from under us.
        logger.warning("publish_silent_failed", reason=str(exc))
        if callback.message is not None:
            with contextlib.suppress(TelegramAPIError):
                await callback.message.edit_text(
                    "Не удалось опубликовать тест. Попробуйте загрузить заново."
                )
        await state.clear()
        return

    await state.clear()
    if callback.message is not None:
        with contextlib.suppress(TelegramAPIError):
            await callback.message.edit_text(_PUBLISHED_SILENT_REPLY)


@router.callback_query(PublishCD.filter(F.action == "publish_notify"))
async def on_publish_notify(
    callback: CallbackQuery,
    callback_data: PublishCD,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """📢 Publish + broadcast to all approved users in the background."""
    await callback.answer()
    services = container.services(session)
    try:
        test = await services.test.publish(callback_data.draft_id, notify=True)
    except ValueError as exc:
        logger.warning("publish_notify_failed", reason=str(exc))
        if callback.message is not None:
            with contextlib.suppress(TelegramAPIError):
                await callback.message.edit_text(
                    "Не удалось опубликовать тест. Попробуйте загрузить заново."
                )
        await state.clear()
        return

    await state.clear()
    if callback.message is not None:
        with contextlib.suppress(TelegramAPIError):
            await callback.message.edit_text(_PUBLISHED_NOTIFY_REPLY)

    # Hand the broadcast off to a background task so this callback can
    # return promptly. The task opens its own session.
    report_chat_id = callback.message.chat.id if callback.message is not None else None
    task = asyncio.create_task(_broadcast_published_test(container, test, report_chat_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# ---------- broadcast background task ----------


async def _broadcast_published_test(
    container: Container,
    test: Test,
    report_chat_id: int | None,
) -> None:
    """Run the new-test broadcast to approved users, then DM the admin the summary.

    Opens its own session so we don't share state with the callback's
    already-committed request transaction.
    """
    async with container.session_factory() as session:
        try:
            services = container.services(session)
            text = (
                await services.settings.get("msg_new_test_broadcast")
            ) or _FALLBACK_BROADCAST_TEXT
            recipients = await services.user.list_approved_for_broadcast()
            summary = await services.notification.broadcast_new_test(text, recipients)
            # Commit the bot_blocked flips the broadcast may have made.
            await session.commit()
        except Exception:
            logger.exception("broadcast_published_test_failed", test_id=test.id)
            return

    if report_chat_id is not None:
        message = (
            f"✅ Рассылка завершена для теста #{test.id}.\n"
            f"Отправлено: {summary.sent}\n"
            f"Заблокировано: {summary.blocked}\n"
            f"Ошибок: {summary.errors}"
        )
        with contextlib.suppress(TelegramAPIError):
            await container.bot.send_message(report_chat_id, message)


# ---------- upload-flow helpers ----------


async def _send(message: Message, rendered: RenderedMessage) -> None:
    """Send a pure text :class:`RenderedMessage` (admin-flow screens carry no photo)."""
    await message.answer(
        rendered.text,
        reply_markup=rendered.reply_markup,
        parse_mode=rendered.parse_mode,
    )


async def _show_preview(
    message: Message,
    state: FSMContext,
    *,
    test: Test,
    services: Services,
) -> None:
    """Move to ``confirming_publish`` and render the publish/cancel preview."""
    image_count = await services.test.count_image_questions(test.id)
    await state.set_state(AdminTestUploadState.confirming_publish)
    await state.update_data(draft_test_id=test.id)
    await _send(message, render_test_preview(test, image_count=image_count))


async def _finish_image_collection(
    message: Message,
    state: FSMContext,
    draft_id: int,
    services: Services,
) -> None:
    """All illustrations collected — re-fetch the draft and show the preview."""
    test = await services.test.get_test(draft_id)
    if test is None:
        await state.clear()
        await message.answer(_IMAGE_FLOW_LOST)
        return
    await _show_preview(message, state, test=test, services=services)
