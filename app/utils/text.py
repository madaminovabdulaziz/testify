"""Text helpers — primarily HTML escaping for Telegram messages.

The bot sends with ``parse_mode=HTML``, so any user-provided string
(full names, rejection reasons, payment-instruction placeholders, etc.)
must be escaped before interpolation. CLAUDE.md treats forgetting this
as a security bug, not a styling oversight.
"""

from __future__ import annotations

import html


def html_escape(text: str | None) -> str:
    """Escape ``<``, ``>``, and ``&`` for safe HTML rendering.

    ``None`` becomes an empty string so callers can pass optional values
    (e.g. ``user.username``) without a guard.
    """
    if text is None:
        return ""
    return html.escape(text, quote=False)


def truncate(text: str, max_len: int, suffix: str = "…") -> str:
    """Trim ``text`` to at most ``max_len`` characters, appending ``suffix``."""
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    cut = max(0, max_len - len(suffix))
    return text[:cut] + suffix


def normalize_phone(raw: str | None) -> str:
    """Canonicalize a phone number to digits-only form (no ``+``).

    Telegram delivers shared-contact numbers sometimes with and sometimes
    without a leading ``+``; admins type ``/find`` queries either way. Storing
    and querying the digits-only form makes them all match (CODE_REVIEW H18).
    A bare 9-digit Uzbek mobile gets the ``998`` country code prepended so it
    lines up with the full international number Telegram usually reports.
    """
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 9:  # local mobile without the 998 country code
        digits = "998" + digits
    return digits


class _SafeDict(dict):  # type: ignore[type-arg]
    """Dict that renders a missing key back as its ``{placeholder}`` literal."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def safe_format(template: str, fills: dict[str, str]) -> str:
    """Like ``template.format(**fills)`` but never raises on bad admin copy.

    Admin-editable copy (the ``settings`` table) can contain an unknown
    ``{placeholder}`` or a stray brace. A plain ``str.format`` would raise
    ``KeyError``/``ValueError`` on every render and surface as "Произошла
    ошибка" to every onboarding user (CODE_REVIEW H17). Here:

    * unknown placeholders are left as-is via :class:`_SafeDict`;
    * a genuinely malformed template (stray ``{``/``}``) falls back to the
      raw text rather than crashing.
    """
    try:
        return template.format_map(_SafeDict(fills))
    except (IndexError, ValueError, KeyError):
        return template
