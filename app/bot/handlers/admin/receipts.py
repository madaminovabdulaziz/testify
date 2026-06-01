"""Admin-group receipt review: ✅ Approve / ❌ Reject (PRODUCT_BLUEPRINT §8.3).

The reject flow is two-step: tapping ❌ Отклонить sets the admin's FSM
to ``AdminRejectReasonState.waiting_for_reason`` and stores the
``receipt_id`` in state data; the next message in the admin group from
that admin is treated as the reason. Typing «отмена» cancels.

Both flows edit the original admin-group notification to show the
resolution + DM the affected user. ``ReceiptAlreadyProcessedError`` is
caught and surfaced as the spec's "Этот чек уже обработан" message.
"""

from __future__ import annotations

from contextlib import suppress

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.callbacks.receipt import ReceiptDecisionCD
from app.bot.filters.admin_only import AdminOnly
from app.bot.keyboards.main_menu import main_menu_keyboard
from app.bot.states.admin import AdminRejectReasonState
from app.core.container import Container
from app.exceptions import ReceiptAlreadyProcessedError, ReceiptUserBannedError
from app.models.user import User
from app.utils.datetime import format_timestamp_local, now_utc
from app.utils.text import html_escape, safe_format

# Rejection reasons are free text typed by the admin; cap them so an
# over-long reason can't blow the ``rejection_reason VARCHAR(500)`` column
# (CODE_REVIEW M19).
_MAX_REJECTION_REASON_LEN = 500

logger = structlog.get_logger()

router = Router(name="admin_receipts")

_FALLBACK_APPROVED_DM = (
    "🎉 Поздравляем! Ваш платёж подтверждён.\n\n"
    "Вот ссылка на закрытый чат студентов:\n{group_invite_link}\n\n"
    "Когда преподаватель опубликует тест, вы получите уведомление."
)
_FALLBACK_REJECTED_DM = (
    "❌ К сожалению, ваш чек не был одобрен.\n\nПричина: {reason}\n\nВы можете отправить новый чек."
)
_REASON_PROMPT = "Укажите причину отказа (или «отмена»):"
_REJECT_CANCELLED = "Отмена. Чек по-прежнему ожидает решения."
_DOUBLE_TAP_ALERT = "Этот чек уже обработан."
_NOT_ADMIN_ALERT = "Только администратор может проверять чеки."
_USER_BANNED_ALERT = "Пользователь заблокирован — чек не одобрен. Сначала снимите блокировку."
_APPROVED_BUT_BLOCKED = (
    "✅ Одобрено, но пользователь заблокировал бота — он не получил ссылку на чат."
)
_REJECT_DONE_ACK = "✅ Чек отклонён, пользователю отправлено уведомление."


def _admin_handle(callback: CallbackQuery) -> str:
    """Build "@username" if available, else "id=12345" for the resolution caption."""
    if callback.from_user.username:
        return "@" + callback.from_user.username
    return f"id={callback.from_user.id}"


def _admin_handle_from_message(message: Message) -> str:
    if message.from_user is None:
        return "—"
    if message.from_user.username:
        return "@" + message.from_user.username
    return f"id={message.from_user.id}"


async def _notify_admin_group(callback: CallbackQuery, text: str) -> None:
    """Surface an edge-case outcome back to the admin group.

    The happy path acks the callback early (so the button stops spinning —
    CODE_REVIEW H7), which spends the one allowed ``answer()``. For the rare
    error branches (double-tap, banned user) we therefore reply in the group
    instead of popping an alert.
    """
    if callback.message is not None:
        with suppress(TelegramAPIError):
            await callback.message.reply(text)


# ---------- approve ----------


@router.callback_query(
    ReceiptDecisionCD.filter(F.decision == "approve"),
)
async def on_approve(
    callback: CallbackQuery,
    callback_data: ReceiptDecisionCD,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """✅ Одобрить — service approve + edit caption + DM the student."""
    # Ack immediately so the button stops spinning during the ~6 round-trips
    # below (approve UPDATE, caption edit, settings reads, student DM). A
    # slow ack is what made admins double-tap and confuse themselves
    # (CODE_REVIEW H7). This spends our one allowed answer(); edge cases
    # below report via a group reply instead.
    await callback.answer()

    services = container.services(session)
    admin = await services.admin.get_by_telegram_id(callback.from_user.id)
    if admin is None:
        # Defense in depth — AdminOnly() at the router should have stopped
        # non-admins already (CODE_REVIEW C1).
        await _notify_admin_group(callback, _NOT_ADMIN_ALERT)
        return

    try:
        approved_user = await services.receipt.approve(callback_data.receipt_id, admin_user=admin)
    except ReceiptUserBannedError:
        await _notify_admin_group(callback, _USER_BANNED_ALERT)
        return
    except ReceiptAlreadyProcessedError:
        await _notify_admin_group(callback, _DOUBLE_TAP_ALERT)
        return

    # Edit the admin-group notification to show the resolution and clear
    # buttons. Escape the admin handle defensively even though Telegram
    # usernames are [A-Za-z0-9_] today (CODE_REVIEW L6).
    resolution = (
        f"✅ Одобрено {html_escape(_admin_handle(callback))} в {format_timestamp_local(now_utc())}"
    )
    if callback.message is not None:
        try:
            await callback.message.edit_caption(caption=resolution, reply_markup=None)
        except TelegramAPIError:
            logger.exception("edit_admin_message_failed", receipt_id=callback_data.receipt_id)

    # DM the now-approved student with the invite link + attach the
    # main menu reply keyboard so they immediately have tappable
    # actions (▶️ Пройти тест / 📜 Мои результаты / 💬 Чат / ❓ Помощь)
    # instead of needing to type slash commands.
    link = (await services.settings.get("group_invite_link")) or ""
    template = (await services.settings.get("msg_approved")) or _FALLBACK_APPROVED_DM
    # The bot sends with parse_mode=HTML globally, so any '&'/'<' in the
    # invite link would break the message — escape before substitution
    # (CODE_REVIEW H19).
    dm_text = safe_format(template, {"group_invite_link": html_escape(link)})
    # Route through NotificationService so a student who blocked the bot is
    # flagged bot_blocked here, not just on the next broadcast (M7); if the
    # DM didn't land, warn the admin so they know the link wasn't delivered
    # (L5).
    delivered = await services.notification.send_user_message(
        approved_user.id,
        approved_user.telegram_id,
        dm_text,
        reply_markup=main_menu_keyboard(),
    )
    if not delivered:
        await _notify_admin_group(callback, _APPROVED_BUT_BLOCKED)


# ---------- reject (two-step) ----------


@router.callback_query(
    ReceiptDecisionCD.filter(F.decision == "reject"),
)
async def on_reject_init(
    callback: CallbackQuery,
    callback_data: ReceiptDecisionCD,
    state: FSMContext,
) -> None:
    """❌ Отклонить — prompt the admin for a reason and wait for the next message."""
    await callback.answer()
    if callback.message is not None:
        await callback.message.reply(_REASON_PROMPT)
    await state.set_state(AdminRejectReasonState.waiting_for_reason)
    await state.update_data(
        receipt_id=callback_data.receipt_id,
        # Remember which message to edit when the reason arrives.
        admin_message_id=callback.message.message_id if callback.message else None,
    )


@router.message(
    StateFilter(AdminRejectReasonState.waiting_for_reason),
    AdminOnly(),
)
async def on_reject_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """The admin's next message after ❌ — either «отмена» or the rejection reason."""
    text = (message.text or "").strip()
    if not text or text.lower() == "отмена":
        await state.clear()
        await message.reply(_REJECT_CANCELLED)
        return

    # Cap before the reason reaches the service / DB / DM. The column is
    # VARCHAR(500); an over-long reason would otherwise raise DataError on
    # flush and leave the receipt stuck pending (CODE_REVIEW M19).
    if len(text) > _MAX_REJECTION_REASON_LEN:
        text = text[:_MAX_REJECTION_REASON_LEN]

    data = await state.get_data()
    receipt_id = data.get("receipt_id")
    admin_message_id = data.get("admin_message_id")
    if not isinstance(receipt_id, int):
        # Stale state; bail.
        await state.clear()
        return

    services = container.services(session)
    admin = await services.admin.get_by_telegram_id(
        message.from_user.id if message.from_user else 0
    )
    if admin is None:
        await state.clear()
        return

    try:
        rejected_user = await services.receipt.reject(receipt_id, admin_user=admin, reason=text)
    except ReceiptAlreadyProcessedError:
        await state.clear()
        await message.reply(_DOUBLE_TAP_ALERT)
        return

    # Edit the original notification.
    if isinstance(admin_message_id, int):
        resolution = (
            f"❌ Отклонено {html_escape(_admin_handle_from_message(message))}: {html_escape(text)}"
        )
        try:
            await container.bot.edit_message_caption(
                chat_id=container.settings.admin_group_id,
                message_id=admin_message_id,
                caption=resolution,
                reply_markup=None,
            )
        except TelegramAPIError:
            logger.exception("edit_admin_message_failed", receipt_id=receipt_id)

    # DM the student. Escape the admin-typed reason before it lands in the
    # HTML-parsed message (a stray '<' or '&' would 400 the send and the
    # student would never learn they were rejected — CODE_REVIEW H19).
    template = (await services.settings.get("msg_rejected")) or _FALLBACK_REJECTED_DM
    dm_text = safe_format(template, {"reason": html_escape(text)})
    # Route through NotificationService for the bot_blocked flip on Forbidden (M7).
    await services.notification.send_user_message(
        rejected_user.id, rejected_user.telegram_id, dm_text
    )

    await state.clear()
    await message.reply(_REJECT_DONE_ACK)
