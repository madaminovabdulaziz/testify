"""Global error handler (registered via ``dispatcher.errors.register``).

ARCHITECTURE_SPEC §16.2:
* ``UserError`` subclasses → DM the user the friendly ``user_message``;
  not logged at error level (it's a normal user-facing rejection).
* Anything else → log + Sentry (if installed) + generic "try later" DM.

Lives under ``middlewares/`` for code-organization reasons (ARCHITECTURE_SPEC
§3 puts it there) even though it's technically registered as an error
handler, not a middleware.
"""

from __future__ import annotations

import contextlib

import structlog
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import ErrorEvent

from app.bot.middlewares._util import get_event_obj
from app.exceptions import UserError

logger = structlog.get_logger()

_GENERIC_USER_MESSAGE = "Произошла ошибка. Попробуйте позже."

# A callback query can only be answered for a short window. When the bot
# answers too late — the loop was briefly busy, or (the common case on a
# platform that redeploys often) Telegram replayed a pre-restart update
# because we set the webhook with ``drop_pending_updates=False`` — the
# ``answerCallbackQuery`` call raises this. It is benign: the user's tap is
# stale, nothing is waiting on the spinner, and there is nothing to tell
# them. Swallow it quietly instead of paging Sentry + DMing a scary error.
_STALE_CALLBACK_MARKERS = ("query is too old", "query id is invalid")


def _is_stale_callback_error(exc: BaseException) -> bool:
    if not isinstance(exc, TelegramBadRequest):
        return False
    message = (exc.message or "").lower()
    return any(marker in message for marker in _STALE_CALLBACK_MARKERS)


async def global_error_handler(event: ErrorEvent) -> bool:
    """Catch every unhandled exception from a handler.

    Returns ``True`` so aiogram treats the error as resolved (no further
    propagation).
    """
    exc = event.exception
    update = event.update

    if isinstance(exc, UserError):
        await _send_user_message(update, exc.user_message)
        return True

    if _is_stale_callback_error(exc):
        # Benign: an expired/replayed callback ack. No Sentry, no user DM.
        logger.debug("stale_callback_answer_ignored")
        return True

    logger.exception("unhandled_exception")
    _capture_to_sentry(exc)
    await _send_user_message(update, _GENERIC_USER_MESSAGE)
    return True


def _capture_to_sentry(exc: BaseException) -> None:
    """Lazy-import sentry_sdk so dev environments without it don't crash."""
    try:
        import sentry_sdk
    except ImportError:
        return
    with contextlib.suppress(Exception):
        sentry_sdk.capture_exception(exc)


async def _send_user_message(update: object, text: str) -> None:
    """Best-effort reply to whatever sub-event raised. Swallow Telegram errors."""
    if update is None:
        return
    event_obj = get_event_obj(update)  # type: ignore[arg-type]
    if event_obj is None or not hasattr(event_obj, "answer"):
        return
    with contextlib.suppress(TelegramAPIError, TypeError):
        # CallbackQuery.answer takes show_alert; Message.answer doesn't.
        # Try the alert form first; fall back to plain text.
        try:
            await event_obj.answer(text, show_alert=True)
        except TypeError:
            await event_obj.answer(text)
