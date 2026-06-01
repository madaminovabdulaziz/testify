"""Admin settings commands: /settings, /set, /preview.

PRODUCT_BLUEPRINT §8.8. The set of allowed keys is the set of rows seeded
by the initial Alembic migration (DATABASE_SPEC §8). ``/set`` rejects any
key outside that set so a typo can't quietly insert dead config.

``/preview <key>`` renders a templated message *as the user would see
it* — placeholders substituted with current settings values (and a
sample reference code where the user-specific value would normally go).
For plain-text keys (no placeholders), it just sends the raw value.
"""

from __future__ import annotations

from typing import Final

import structlog
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters.admin_only import AdminOnly
from app.core.container import Container
from app.models.user import User
from app.utils.text import html_escape, safe_format

logger = structlog.get_logger()

router = Router(name="admin_settings")
router.message.filter(AdminOnly())

# Mirrors DATABASE_SPEC §8 verbatim. A typo in a /set call against an
# unknown key would otherwise silently insert a dead row.
_ALLOWED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "welcome_message",
        "payment_amount",
        "payment_amount_display",
        "payment_card_number",
        "payment_recipient_name",
        "payment_instructions",
        "group_invite_link",
        "support_contact",
        "msg_receipt_accepted",
        "msg_approved",
        "msg_rejected",
        "msg_new_test_broadcast",
        "msg_warning_10min",
        "msg_warning_5min",
        "msg_warning_1min",
        "msg_auto_submitted",
        "msg_already_attempted",
        "msg_no_active_test",
        "msg_banned",
        "phash_hamming_threshold",
    }
)

# Sample data baked into /preview when the user-specific placeholder would
# normally be populated by the live flow.
_PREVIEW_SAMPLES: Final[dict[str, str]] = {
    "reference_code": "SAMPLE",
    "reason": "пример причины отказа",
    "score": "42",
}

_USAGE_SET = "Использование: /set <ключ> <значение>"
_USAGE_PREVIEW = "Использование: /preview <ключ>"
_UNKNOWN_KEY_TEMPLATE = "❌ Неизвестный ключ: <code>{key}</code>\n\nДоступные ключи: /settings"


# ============================================================
# /settings — list current values
# ============================================================


@router.message(Command("settings"))
async def cmd_settings(
    message: Message,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    services = container.services(session)
    all_settings = await services.settings.get_all()
    await message.answer(_render_settings_list(all_settings), parse_mode="HTML")


def _render_settings_list(settings_map: dict[str, str]) -> str:
    lines = ["⚙️ <b>Текущие настройки</b>", ""]
    # Sort alphabetically for stable rendering.
    for key in sorted(_ALLOWED_KEYS):
        value = settings_map.get(key, "")
        preview = _short_preview(value)
        lines.append(f"<code>{html_escape(key)}</code> — {preview}")
    lines.append("")
    lines.append("Изменить: <code>/set &lt;ключ&gt; &lt;значение&gt;</code>")
    lines.append("Посмотреть полностью: <code>/preview &lt;ключ&gt;</code>")
    return "\n".join(lines)


def _short_preview(value: str) -> str:
    """One-line snippet of a value for the /settings table."""
    if not value:
        return "<i>(пусто)</i>"
    first_line = value.splitlines()[0]
    if len(first_line) > 60:
        first_line = first_line[:59] + "…"
    return html_escape(first_line)


# ============================================================
# /set <key> <value>
# ============================================================


@router.message(Command("set"))
async def cmd_set(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    raw = (command.args or "").strip()
    if not raw or " " not in raw:
        await message.answer(_USAGE_SET)
        return

    key, value = raw.split(maxsplit=1)
    key = key.strip()
    value = value.strip()

    if key not in _ALLOWED_KEYS:
        await message.answer(
            _UNKNOWN_KEY_TEMPLATE.format(key=html_escape(key)),
            parse_mode="HTML",
        )
        return

    services = container.services(session)
    admin = await services.admin.get_by_telegram_id(message.from_user.id)
    admin_id = admin.id if admin is not None else None

    await services.settings.set(key, value, admin_id)
    logger.info(
        "admin_set_setting",
        admin_telegram_id=message.from_user.id,
        key=key,
        value_len=len(value),
    )
    await message.answer(
        f"✅ <code>{html_escape(key)}</code> обновлено.",
        parse_mode="HTML",
    )


# ============================================================
# /preview <key>
# ============================================================


@router.message(Command("preview"))
async def cmd_preview(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    key = (command.args or "").strip().split()[0:1]
    if not key:
        await message.answer(_USAGE_PREVIEW)
        return
    key_name = key[0]

    # Allow both "welcome" and "welcome_message" — the spec example uses
    # the bare token. Map back to the canonical key.
    canonical = _resolve_preview_alias(key_name)
    if canonical not in _ALLOWED_KEYS:
        await message.answer(
            _UNKNOWN_KEY_TEMPLATE.format(key=html_escape(key_name)),
            parse_mode="HTML",
        )
        return

    services = container.services(session)
    raw_value = (await services.settings.get(canonical)) or ""
    if not raw_value:
        await message.answer(
            f"⚠️ Значение для <code>{html_escape(canonical)}</code> пустое.",
            parse_mode="HTML",
        )
        return

    # Substitute placeholders with current settings values where the
    # template references them; sample fillers for user-context vars.
    rendered = await _substitute_placeholders(raw_value, services_settings=services.settings)
    await message.answer(
        "📤 Так увидит пользователь:\n\n" + rendered,
    )


_PREVIEW_ALIASES: Final[dict[str, str]] = {
    "welcome": "welcome_message",
    "payment": "payment_instructions",
    "approved": "msg_approved",
    "rejected": "msg_rejected",
}


def _resolve_preview_alias(token: str) -> str:
    """Map a user-typed shorthand to the canonical settings key."""
    return _PREVIEW_ALIASES.get(token, token)


async def _substitute_placeholders(template: str, *, services_settings) -> str:  # type: ignore[no-untyped-def]
    """Render template-style ``{placeholders}`` with current settings + sample fillers.

    Missing placeholders are kept literal — never crash on a typo in
    the template itself.
    """
    # Pre-load values the templates can reference. Keep this list in
    # sync with the placeholders documented in DATABASE_SPEC §8.
    setting_keys = (
        "amount_display",
        "card_number",
        "recipient_name",
        "group_invite_link",
    )
    fills: dict[str, str] = {}
    for key in setting_keys:
        # Settings stores ``payment_<key>`` and ``group_invite_link``;
        # the placeholders in the templates use the bare name.
        looked_up = (await services_settings.get(_settings_key_for_placeholder(key))) or ""
        fills[key] = looked_up
    fills.update(_PREVIEW_SAMPLES)
    return safe_format(template, fills)


def _settings_key_for_placeholder(placeholder: str) -> str:
    """Map a template placeholder name to the row key in ``settings``."""
    if placeholder == "amount_display":
        return "payment_amount_display"
    if placeholder == "card_number":
        return "payment_card_number"
    if placeholder == "recipient_name":
        return "payment_recipient_name"
    return placeholder
