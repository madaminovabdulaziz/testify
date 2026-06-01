"""Helpers shared by the middleware chain.

Pulling the sender or update kind out of an ``aiogram.types.Update`` is
the same boilerplate for logging, user-loading and throttling, so it
lives here once.
"""

from __future__ import annotations

from typing import Any

from aiogram.types import Update, User

# Order matters: we return the first populated event field, mirroring
# Telegram's own one-of semantics. Common-case events come first to
# short-circuit the loop faster.
_UPDATE_EVENT_ATTRS: tuple[str, ...] = (
    "message",
    "callback_query",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "inline_query",
    "chosen_inline_result",
    "shipping_query",
    "pre_checkout_query",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
)


def get_event_obj(update: Update) -> Any | None:
    """Return the populated subfield of ``update`` (Message, CallbackQuery, …)."""
    for attr in _UPDATE_EVENT_ATTRS:
        event_obj = getattr(update, attr, None)
        if event_obj is not None:
            return event_obj
    return None


def get_from_user(update: Update) -> User | None:
    """Return ``from_user`` if the contained event has one."""
    event_obj = get_event_obj(update)
    if event_obj is None:
        return None
    return getattr(event_obj, "from_user", None)


def get_update_type(update: Update) -> str:
    """Short string naming the update's kind, e.g. ``message`` or ``callback_query``."""
    for attr in _UPDATE_EVENT_ATTRS:
        if getattr(update, attr, None) is not None:
            return attr
    # ``poll`` isn't in the user-bearing list above, but we still want it
    # labelled for log context.
    if getattr(update, "poll", None) is not None:
        return "poll"
    return "unknown"


def get_chat_id(update: Update) -> int | None:
    """Return the chat ID the update came from, if any.

    Messages expose ``.chat`` directly; callback_queries route through
    ``.message.chat``. Returns ``None`` for events without a chat
    (inline_query, etc.).
    """
    event_obj = get_event_obj(update)
    if event_obj is None:
        return None
    chat = getattr(event_obj, "chat", None)
    if chat is None:
        inner = getattr(event_obj, "message", None)
        chat = getattr(inner, "chat", None) if inner is not None else None
    return getattr(chat, "id", None) if chat is not None else None
