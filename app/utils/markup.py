"""Lightweight inline markup for question text: **жирный** and __курсив__.

The teacher writes double-asterisk / double-underscore markers directly in
the Excel cell or the web editor; the bot converts them to Telegram HTML
entities at render time (ARCHITECTURE_SPEC §21.4, enabled by client
request). The markers are stored verbatim in the DB — conversion is a
pure render-time concern, so the web editor and Excel round-trip always
show the author exactly what they typed.

Safety: :func:`render_markup` escapes the text FIRST, then swaps marker
pairs for ``<b>``/``<i>`` — author-typed ``<`` or ``&`` can never become
markup. Nesting works (``**__слово__**`` → ``<b><i>слово</i></b>``),
matching what Telegram supports.
"""

from __future__ import annotations

import re
from typing import Final

from app.utils.text import html_escape

BOLD_MARKER: Final[str] = "**"
ITALIC_MARKER: Final[str] = "__"

_BOLD_RE: Final[re.Pattern[str]] = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE: Final[re.Pattern[str]] = re.compile(r"__(.+?)__", re.DOTALL)

MSG_UNBALANCED_BOLD: Final[str] = (
    "Непарное выделение **жирным**: маркеры ** должны открываться и закрываться."
)
MSG_UNBALANCED_ITALIC: Final[str] = (
    "Непарное выделение __курсивом__: маркеры __ должны открываться и закрываться."
)
MSG_EMPTY_MARKUP: Final[str] = (
    "Пустое выделение (** ** или __ __) — добавьте текст между маркерами."
)


def validate_markup(text: str) -> list[str]:
    """Russian error messages for malformed markup; empty list when clean.

    Rules: every ``**`` and ``__`` must come in pairs, and a pair must
    enclose at least one non-space character — otherwise students would
    see stray markers verbatim.
    """
    errors: list[str] = []
    if text.count(BOLD_MARKER) % 2 != 0:
        errors.append(MSG_UNBALANCED_BOLD)
    if text.count(ITALIC_MARKER) % 2 != 0:
        errors.append(MSG_UNBALANCED_ITALIC)
    if _has_empty_pair(text, _BOLD_RE) or _has_empty_pair(text, _ITALIC_RE):
        errors.append(MSG_EMPTY_MARKUP)
    return errors


def render_markup(text: str | None) -> str:
    """HTML-escape ``text`` and convert marker pairs to Telegram entities.

    Escape-first ordering keeps this XSS-safe; unpaired leftovers render
    verbatim (the validators reject them at authoring time, this is just
    the graceful fallback for pre-existing rows).
    """
    escaped = html_escape(text)
    converted = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    return _ITALIC_RE.sub(r"<i>\1</i>", converted)


def visible_length(text: str) -> int:
    """Length of the text as the student sees it (marker chars removed).

    Telegram's message/caption limits apply to the parsed text, not the
    raw markers — the caption-budget check must therefore measure the
    rendered length.
    """
    without_bold = _BOLD_RE.sub(r"\1", text)
    return len(_ITALIC_RE.sub(r"\1", without_bold))


def _has_empty_pair(text: str, pattern: re.Pattern[str]) -> bool:
    return any(not match.group(1).strip() for match in pattern.finditer(text))
