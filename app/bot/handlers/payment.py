"""Payment handlers: «Я оплатил» tap → photo upload → admin-group post.

Implements PRODUCT_BLUEPRINT §8.2. Duplicate detection, the
3-pending-per-user cap, and the user-status flip to ``pending_approval``
are all handled by :class:`ReceiptService` — this layer just downloads
the photo bytes (needed for pHash), calls the service, and forwards
the photo + caption to the admin group.
"""

from __future__ import annotations

from io import BytesIO

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.payment import I_PAID_CALLBACK
from app.bot.states.payment import PaymentState
from app.bot.views.admin_receipt import render_admin_receipt_notification
from app.core.container import Container
from app.exceptions import ReceiptAlreadyPendingError, ReceiptLimitExceededError
from app.models.user import User
from app.services.receipt_service import ReceiptWarning

logger = structlog.get_logger()

router = Router(name="payment")

_RECEIPT_PROMPT = "📸 Отправьте, пожалуйста, фото чека одним сообщением."
_NON_PHOTO_REMINDER = "Пожалуйста, отправьте фото чека"  # matches PRODUCT_BLUEPRINT §8.2 (N1)
_ALREADY_APPROVED = "Вы уже студент. Дополнительная оплата не требуется."
_FALLBACK_ACCEPTED = "✅ Чек получен. Мы проверим его в ближайшее время и сообщим вам о решении."
_FILE_TOO_LARGE = "Файл слишком большой. Максимум 5 МБ."
_DOWNLOAD_FAILED = "Не удалось скачать чек. Попробуйте отправить ещё раз."
_BAD_IMAGE = "Не удалось обработать изображение. Отправьте, пожалуйста, чёткое фото чека."
_SUBMIT_IN_PROGRESS = (
    "Ваш предыдущий чек ещё обрабатывается. Подождите пару секунд и попробуйте снова."
)

# PRODUCT_BLUEPRINT §13: receipts are photos; Telegram caps photos at ~10 MB
# but we only accept up to 5 MB to keep the in-memory pHash decode bounded.
_MAX_RECEIPT_BYTES = 5 * 1024 * 1024
# How long the per-user submit lock lives if a handler dies before releasing
# it (it's always released in a finally; this is just the safety expiry).
_SUBMIT_LOCK_TTL_SECONDS = 10
# Media-group dedup marker TTL — comfortably longer than a media group takes
# to arrive as separate updates.
_MEDIA_GROUP_TTL_SECONDS = 60

# Maps each soft anti-fraud signal from ReceiptService to its admin-group
# caption line (CODE_REVIEW M8/M9/M10). Copy lives here, not in the service.
_RECEIPT_WARNING_TEXT: dict[ReceiptWarning, str] = {
    ReceiptWarning.DUPLICATE_APPROVED: "⚠️ Похожий чек уже был одобрен ранее.",
    ReceiptWarning.DUPLICATE_REJECTED: "⚠️ Похожий чек был ранее отклонён.",
    ReceiptWarning.DUPLICATE_PENDING_OTHER: "⚠️ Похожий чек уже отправлен другим пользователем.",
    ReceiptWarning.PHONE_REUSED: "⚠️ Этот телефон уже привязан к одобренному пользователю.",
}


@router.callback_query(F.data == I_PAID_CALLBACK)
async def on_i_paid(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
) -> None:
    """User tapped «Я оплатил, отправить чек»."""
    await callback.answer()
    if user.status == "approved":
        if callback.message is not None:
            await callback.message.answer(_ALREADY_APPROVED)
        return
    await state.set_state(PaymentState.waiting_for_receipt)
    if callback.message is not None:
        await callback.message.answer(_RECEIPT_PROMPT)


@router.message(StateFilter(PaymentState.waiting_for_receipt), F.photo)
async def on_receipt_photo(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """User sent a photo while we were waiting for the receipt."""
    if user.status == "approved":
        await message.answer(_ALREADY_APPROVED)
        await state.clear()
        return

    env = container.settings.env

    # M3: a media group (several photos in one message) is delivered as N
    # separate updates that share a media_group_id. Process only the first;
    # the SETNX marker makes every later photo of the group a silent no-op
    # (PRODUCT_BLUEPRINT §13 — "process only the first photo").
    if message.media_group_id is not None:
        group_key = f"{env}:receipt_mediagroup:{message.media_group_id}"
        is_first = await container.redis.set(group_key, "1", nx=True, ex=_MEDIA_GROUP_TTL_SECONDS)
        if not is_first:
            return

    photo = message.photo[-1] if message.photo else None
    if photo is None:
        # Shouldn't happen — the F.photo filter guarantees it.
        await message.answer(_NON_PHOTO_REMINDER)
        return

    # H8: reject oversized files before pulling them into memory.
    if photo.file_size and photo.file_size > _MAX_RECEIPT_BYTES:
        await message.answer(_FILE_TOO_LARGE)
        return

    # H8: a flaky download must surface an actionable message, not fall
    # through to the generic "try later" of the global error handler.
    buf = BytesIO()
    try:
        await container.bot.download(photo.file_id, destination=buf)
    except TelegramAPIError:
        logger.warning("receipt_download_failed", user_id=user.id)
        await message.answer(_DOWNLOAD_FAILED)
        return
    photo_bytes = buf.getvalue()

    services = container.services(session)
    settings_svc = services.settings

    # C6: serialize this user's receipt submissions. The "≤ 3 pending"
    # cap is a count-then-insert in ReceiptService.submit; firing several
    # photos at once (or a media group, before M3 above caught it) could
    # otherwise slip past the count. A per-user NX lock makes concurrent
    # submissions wait their turn; a loser is told to retry rather than
    # double-inserting. Released in the finally below.
    lock_key = f"{env}:receipt_submit_lock:{user.id}"
    got_lock = await container.redis.set(lock_key, "1", nx=True, ex=_SUBMIT_LOCK_TTL_SECONDS)
    if not got_lock:
        await message.answer(_SUBMIT_IN_PROGRESS)
        return

    try:
        try:
            result = await services.receipt.submit(
                user,
                photo_file_id=photo.file_id,
                photo_file_unique_id=photo.file_unique_id,
                photo_bytes=photo_bytes,
            )
        except (ReceiptAlreadyPendingError, ReceiptLimitExceededError) as exc:
            # Service-side guards — surface the user-friendly message.
            await message.answer(exc.user_message)
            return
        except ValueError:
            # ImageHasher couldn't decode the bytes as an image (H8).
            logger.warning("receipt_decode_failed", user_id=user.id)
            await message.answer(_BAD_IMAGE)
            return

        # Compose the admin notification using a fresh view.
        warnings = [_RECEIPT_WARNING_TEXT[w] for w in result.warnings]
        rendered = render_admin_receipt_notification(user, result.receipt, warnings=warnings)

        try:
            admin_msg = await container.bot.send_photo(
                chat_id=container.settings.admin_group_id,
                photo=photo.file_id,
                caption=rendered.text,
                reply_markup=rendered.reply_markup,
            )
            await services.receipt.attach_admin_notification_message(
                result.receipt.id, admin_msg.message_id
            )
        except TelegramAPIError:
            # Don't block the user's accepted-DM on an admin-group send failure;
            # the admin can still resolve via /find + manual approve.
            logger.exception(
                "admin_group_post_failed",
                receipt_id=result.receipt.id,
                user_id=user.id,
            )

        accepted_text = (await settings_svc.get("msg_receipt_accepted")) or _FALLBACK_ACCEPTED
        await message.answer(accepted_text)
        await state.clear()
    finally:
        await container.redis.delete(lock_key)


@router.message(StateFilter(PaymentState.waiting_for_receipt))
async def remind_send_photo(message: Message) -> None:
    """Non-photo message while waiting for receipt: gentle nudge."""
    await message.answer(_NON_PHOTO_REMINDER)
