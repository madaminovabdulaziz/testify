"""Admin announcement broadcast: compose in Telegram → preview → confirm → fan-out.

The admin writes the announcement as a normal Telegram message — bold,
italic, emoji, a photo, video or GIF with caption — and the bot replays
it to every approved student via ``copyMessage``, which preserves the
formatting entities and media exactly (and shows no "forwarded from"
header). Before anything is sent, the bot copies the message back to
the admin as a preview with an explicit confirm step.

Delivery is durable: confirming creates a ``broadcasts`` row, and
:mod:`app.jobs.broadcast_runner` advances a per-recipient cursor with
committed progress — a restart resumes instead of half-delivering.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.callbacks.broadcast import BroadcastConfirmCD
from app.bot.filters.admin_only import AdminOnly
from app.bot.keyboards.admin_panel import admin_cancel_keyboard, admin_panel_keyboard
from app.bot.states.admin import AdminBroadcastState
from app.core.container import Container
from app.core.i18n import BTN_ADMIN_BROADCAST, BTN_ADMIN_CANCEL
from app.jobs.broadcast_runner import spawn_broadcast
from app.models.user import User

logger = structlog.get_logger()

router = Router(name="admin_broadcast")
router.message.filter(AdminOnly())
router.callback_query.filter(AdminOnly())

# Content types copyMessage replays faithfully and students expect to receive.
_ALLOWED_CONTENT = ("text", "photo", "video", "animation", "document", "audio", "voice")

_PROMPT = (
    "📣 <b>Рассылка всем студентам</b>\n\n"
    "Отправьте сообщение — оно будет доставлено каждому оплатившему "
    "студенту в точности как вы его написали:\n"
    "• текст с форматированием (жирный, курсив, эмодзи);\n"
    "• фото, видео или GIF с подписью.\n\n"
    "Перед отправкой я покажу предпросмотр."
)
_UNSUPPORTED = (
    "Этот тип сообщения нельзя разослать. Отправьте текст, фото, видео, GIF, документ или аудио."
)
_ALBUM_NOT_SUPPORTED = (
    "Альбомы (несколько фото в одном сообщении) не поддерживаются. Отправьте одно фото с подписью."
)
_NO_RECIPIENTS = "Сейчас нет ни одного одобренного студента — рассылать некому."
_PREVIEW_HEADER = "👆 Так студенты увидят сообщение. Отправить?"
_CANCELLED = "Рассылка отменена."
_STARTED = (
    "🚀 Рассылка #{broadcast_id} запущена ({total} получателей).\n"
    "Я сообщу здесь, когда она завершится.\n\n"
    "⚠️ Не удаляйте исходное сообщение, пока рассылка не закончится."
)
_PREVIEW_LOST = "Не удалось продолжить. Начните заново: /broadcast"
_ALREADY_RUNNING = "Эта рассылка уже запущена."


def _confirm_keyboard(total: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Отправить ({total})",
                    callback_data=BroadcastConfirmCD(action="send").pack(),
                ),
                InlineKeyboardButton(
                    text="🗑 Отменить",
                    callback_data=BroadcastConfirmCD(action="cancel").pack(),
                ),
            ]
        ]
    )


# ---------- entry points ----------


@router.message(Command("broadcast"), F.chat.type == "private")
@router.message(F.text == BTN_ADMIN_BROADCAST, F.chat.type == "private")
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    """Open the compose step (private chat only — the source message must live there)."""
    await state.set_state(AdminBroadcastState.waiting_for_message)
    await message.answer(_PROMPT, reply_markup=admin_cancel_keyboard())


@router.message(Command("broadcast"))
async def cmd_broadcast_in_group(message: Message) -> None:
    """In the admin group the flow would leak drafts — DM only."""
    await message.answer("Команда работает только в личных сообщениях с ботом.")


# ---------- cancel (button or /cancel) while composing/confirming ----------


@router.message(StateFilter(AdminBroadcastState), F.text == BTN_ADMIN_CANCEL)
@router.message(StateFilter(AdminBroadcastState), Command("cancel"))
async def broadcast_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(_CANCELLED, reply_markup=admin_panel_keyboard())


# ---------- compose step: the next message is the announcement ----------


@router.message(StateFilter(AdminBroadcastState.waiting_for_message))
async def on_announcement_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Validate the composed message, echo a preview, ask for confirmation."""
    if message.media_group_id is not None:
        await message.answer(_ALBUM_NOT_SUPPORTED)
        return
    if message.content_type not in _ALLOWED_CONTENT:
        await message.answer(_UNSUPPORTED)
        return

    services = container.services(session)
    total = await services.broadcast.count_recipients()
    if total == 0:
        await state.clear()
        await message.answer(_NO_RECIPIENTS, reply_markup=admin_panel_keyboard())
        return

    # Preview = copy the message back, exactly as students will receive it.
    try:
        await container.bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except TelegramAPIError:
        logger.exception("announcement_preview_failed")
        await message.answer(_UNSUPPORTED)
        return

    await state.set_state(AdminBroadcastState.confirming)
    await state.update_data(
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    await message.answer(_PREVIEW_HEADER, reply_markup=_confirm_keyboard(total))


# ---------- confirm / cancel callbacks ----------


@router.callback_query(BroadcastConfirmCD.filter(F.action == "cancel"))
async def on_confirm_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    if callback.message is not None:
        try:
            await callback.message.edit_text(_CANCELLED)
        except TelegramAPIError:
            logger.info("broadcast_cancel_edit_failed")


@router.callback_query(BroadcastConfirmCD.filter(F.action == "send"))
async def on_confirm_send(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Create the durable broadcast row, commit, then start delivery."""
    await callback.answer()

    # Idempotency: a double-tap finds the state already cleared.
    if await state.get_state() != AdminBroadcastState.confirming.state:
        if callback.message is not None:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                logger.info("broadcast_doubletap_edit_failed")
        return

    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    await state.clear()
    if not isinstance(source_chat_id, int) or not isinstance(source_message_id, int):
        if callback.message is not None:
            await callback.message.answer(_PREVIEW_LOST)
        return

    services = container.services(session)
    admin = await services.admin.get_by_telegram_id(callback.from_user.id)
    report_chat_id = callback.message.chat.id if callback.message is not None else None

    broadcast = await services.broadcast.create(
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        created_by_admin_id=admin.id if admin is not None else None,
        report_chat_id=report_chat_id,
    )
    # Commit explicitly BEFORE spawning: the runner reads the row in its own
    # session, so it must be visible outside this request's transaction.
    await session.commit()
    spawn_broadcast(container, broadcast.id)

    logger.info(
        "announcement_started",
        broadcast_id=broadcast.id,
        total=broadcast.total_recipients,
        admin_telegram_id=callback.from_user.id,
    )
    if callback.message is not None:
        try:
            await callback.message.edit_text(
                _STARTED.format(broadcast_id=broadcast.id, total=broadcast.total_recipients)
            )
        except TelegramAPIError:
            logger.info("broadcast_started_edit_failed")
