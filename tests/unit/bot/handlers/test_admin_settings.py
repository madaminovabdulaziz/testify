"""Unit tests for the admin /settings /set /preview commands."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.admin.settings import cmd_preview, cmd_set, cmd_settings


def _admin_user() -> SimpleNamespace:
    return SimpleNamespace(id=99)


def _command(args: str | None = "") -> SimpleNamespace:
    return SimpleNamespace(args=args)


def _message(*, from_id: int = 900) -> MagicMock:
    msg = MagicMock()
    msg.answer = AsyncMock()
    msg.from_user = SimpleNamespace(id=from_id, username="admin")
    return msg


def _container(services: MagicMock) -> MagicMock:
    container = MagicMock()
    container.services = MagicMock(return_value=services)
    return container


def _settings_service(
    *,
    values: dict[str, str] | None = None,
    get_all_returns: dict[str, str] | None = None,
) -> MagicMock:
    values = values or {}
    svc = MagicMock()

    async def fake_get(key: str) -> str | None:
        return values.get(key)

    svc.get = AsyncMock(side_effect=fake_get)
    svc.set = AsyncMock()
    svc.get_all = AsyncMock(return_value=get_all_returns or values)
    return svc


# ============================================================
# /settings
# ============================================================


async def test_settings_lists_every_allowed_key_with_value_preview() -> None:
    services = MagicMock()
    services.settings = _settings_service(
        get_all_returns={
            "welcome_message": "Здравствуйте!",
            "payment_amount": "150000",
            "group_invite_link": "",  # empty
        }
    )
    container = _container(services)
    message = _message()

    await cmd_settings(
        message,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    text = message.answer.await_args.args[0]
    # Includes the values we set …
    assert "Здравствуйте!" in text
    assert "150000" in text
    # … and notes empty rows.
    assert "(пусто)" in text
    # Shows every allowed key — sample a few that weren't in the values dict.
    assert "msg_warning_10min" in text
    assert "support_contact" in text


# ============================================================
# /set
# ============================================================


async def test_set_requires_key_and_value() -> None:
    services = MagicMock()
    services.settings = _settings_service()
    container = _container(services)
    message = _message()

    await cmd_set(
        message,
        command=_command("only_key"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.settings.set.assert_not_awaited()
    assert "Использование" in message.answer.await_args.args[0]


async def test_set_rejects_unknown_key() -> None:
    services = MagicMock()
    services.settings = _settings_service()
    services.admin.get_by_telegram_id = AsyncMock(return_value=SimpleNamespace(id=1))
    container = _container(services)
    message = _message()

    await cmd_set(
        message,
        command=_command("not_a_real_key some value here"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.settings.set.assert_not_awaited()
    assert "Неизвестный ключ" in message.answer.await_args.args[0]


async def test_set_persists_value_and_invokes_service_with_admin_id() -> None:
    services = MagicMock()
    services.settings = _settings_service()
    services.admin.get_by_telegram_id = AsyncMock(return_value=SimpleNamespace(id=42))
    container = _container(services)
    message = _message()

    await cmd_set(
        message,
        command=_command("group_invite_link https://t.me/+abcdef"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.settings.set.assert_awaited_once_with("group_invite_link", "https://t.me/+abcdef", 42)
    assert "обновлено" in message.answer.await_args.args[0]


# ============================================================
# /preview
# ============================================================


async def test_preview_requires_arg() -> None:
    services = MagicMock()
    services.settings = _settings_service()
    container = _container(services)
    message = _message()

    await cmd_preview(
        message,
        command=_command(None),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    assert "Использование" in message.answer.await_args.args[0]


async def test_preview_rejects_unknown_key() -> None:
    services = MagicMock()
    services.settings = _settings_service()
    container = _container(services)
    message = _message()

    await cmd_preview(
        message,
        command=_command("not_a_real_key"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    assert "Неизвестный ключ" in message.answer.await_args.args[0]


async def test_preview_welcome_uses_alias() -> None:
    services = MagicMock()
    services.settings = _settings_service(values={"welcome_message": "Здравствуйте! Это бот."})
    container = _container(services)
    message = _message()

    await cmd_preview(
        message,
        command=_command("welcome"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    text = message.answer.await_args.args[0]
    assert "Здравствуйте! Это бот." in text


async def test_preview_substitutes_payment_placeholders_with_current_settings() -> None:
    services = MagicMock()
    services.settings = _settings_service(
        values={
            "payment_instructions": (
                "💰 {amount_display} 💳 {card_number} 👤 {recipient_name} Код: #{reference_code}"
            ),
            "payment_amount_display": "150 000 сум",
            "payment_card_number": "8600 1234 5678 9012",
            "payment_recipient_name": "Дильноза опа",
        }
    )
    container = _container(services)
    message = _message()

    await cmd_preview(
        message,
        command=_command("payment_instructions"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    text = message.answer.await_args.args[0]
    assert "150 000 сум" in text
    assert "8600 1234 5678 9012" in text
    assert "Дильноза опа" in text
    assert "#SAMPLE" in text  # the sample reference_code filler


async def test_preview_empty_value_warns() -> None:
    services = MagicMock()
    services.settings = _settings_service(values={"group_invite_link": ""})
    container = _container(services)
    message = _message()

    await cmd_preview(
        message,
        command=_command("group_invite_link"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    assert "пустое" in message.answer.await_args.args[0]
