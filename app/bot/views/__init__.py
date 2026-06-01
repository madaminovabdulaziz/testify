"""View layer: pure ``state -> (text, reply_markup)`` functions.

Per ARCHITECTURE_SPEC §9, views never touch the DB, never call
services, and never await anything. Handlers fetch whatever they need
and pass concrete values in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RenderedMessage:
    """A pre-built message handlers send via ``answer`` / ``edit_text``.

    When ``photo_file_id`` is set the message is a Telegram *photo* — ``text``
    becomes the photo caption and the transport layer sends it via
    ``answer_photo`` / ``edit_message_media`` instead of ``answer`` /
    ``edit_text``. This is how illustrated questions reach the student (the
    file id is Telegram's, re-sent without re-upload). ``None`` keeps the
    plain-text behaviour every other screen relies on.
    """

    text: str
    reply_markup: Any = None  # InlineKeyboardMarkup | ReplyKeyboardMarkup | None
    parse_mode: str = "HTML"
    photo_file_id: str | None = None
