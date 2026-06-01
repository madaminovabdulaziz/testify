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
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

from app.bot.middlewares._util import get_event_obj
from app.exceptions import UserError

logger = structlog.get_logger()

_GENERIC_USER_MESSAGE = "Произошла ошибка. Попробуйте позже."


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
