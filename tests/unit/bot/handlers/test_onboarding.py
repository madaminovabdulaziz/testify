"""Unit tests for the onboarding handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.onboarding import (
    on_contact_shared,
    on_name_entered,
    on_start_onboarding,
)
from app.bot.states.onboarding import OnboardingState
from app.exceptions import InvalidNameError


def _user(status: str = "new") -> SimpleNamespace:
    # ``reference_code`` is the field assigned during onboarding — the
    # post-refresh user object the payment screen renders against has it.
    return SimpleNamespace(
        id=1, telegram_id=100, username="alice", reference_code="A7F2K9", status=status
    )


def _services(*, set_name_raises=None, ref_code="A7F2K9") -> MagicMock:
    services = MagicMock()
    services.user.start_onboarding = AsyncMock()
    services.user.set_phone = AsyncMock()
    if set_name_raises:
        services.user.set_name = AsyncMock(side_effect=set_name_raises)
    else:
        services.user.set_name = AsyncMock()
    services.user.attach_reference_code = AsyncMock()
    refreshed_user = SimpleNamespace(
        id=1, telegram_id=100, username="alice", reference_code=ref_code
    )
    services.user.get_user = AsyncMock(return_value=refreshed_user)
    services.user.get_or_create = AsyncMock(return_value=refreshed_user)
    services.ref_code.generate_unique = AsyncMock(return_value=ref_code)

    async def fake_setting(key: str) -> str | None:
        return {
            "payment_instructions": "Код: #{reference_code}",
            "payment_amount_display": "150 000",
            "payment_card_number": "8600",
            "payment_recipient_name": "X",
            "support_contact": "",
        }.get(key)

    services.settings.get = AsyncMock(side_effect=fake_setting)
    return services


def _container(services: MagicMock) -> MagicMock:
    container = MagicMock()
    container.services = MagicMock(return_value=services)
    return container


# ---------- on_start_onboarding ----------


async def test_start_onboarding_advances_status_and_sets_state() -> None:
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.answer = AsyncMock()

    state = MagicMock()
    state.set_state = AsyncMock()

    services = _services()
    container = _container(services)

    await on_start_onboarding(
        callback,
        state=state,
        session=MagicMock(),
        user=_user(),
        container=container,
    )

    services.user.start_onboarding.assert_awaited_once_with(1)
    state.set_state.assert_awaited_once_with(OnboardingState.waiting_for_phone)
    callback.message.answer.assert_awaited_once()


async def test_start_onboarding_refuses_non_new_user() -> None:
    # A stale «Начать» tap from an already-onboarded user must not restart
    # onboarding (it would overwrite their reference_code) — CODE_REVIEW H6.
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    services = _services()
    container = _container(services)

    await on_start_onboarding(
        callback,
        state=state,
        session=MagicMock(),
        user=_user(status="approved"),
        container=container,
    )

    services.user.start_onboarding.assert_not_awaited()
    state.set_state.assert_not_awaited()
    callback.message.answer.assert_awaited_once()


# ---------- on_contact_shared ----------


async def test_contact_shared_persists_phone_and_advances_state() -> None:
    message = MagicMock()
    message.from_user = SimpleNamespace(id=100)
    message.contact = SimpleNamespace(phone_number="+998901234567", user_id=100)
    message.answer = AsyncMock()

    state = MagicMock()
    state.set_state = AsyncMock()

    services = _services()
    container = _container(services)

    await on_contact_shared(
        message,
        state=state,
        session=MagicMock(),
        user=_user(),
        container=container,
    )

    services.user.set_phone.assert_awaited_once_with(1, "+998901234567")
    state.set_state.assert_awaited_once_with(OnboardingState.waiting_for_name)
    message.answer.assert_awaited_once()


# ---------- on_name_entered ----------


async def test_name_entered_invalid_replies_with_message_and_stays_in_state() -> None:
    message = MagicMock()
    message.text = "x"  # too short
    message.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()

    services = _services(set_name_raises=InvalidNameError())
    container = _container(services)

    await on_name_entered(
        message,
        state=state,
        session=MagicMock(),
        user=_user(),
        container=container,
    )

    message.answer.assert_awaited_once_with(InvalidNameError.user_message)
    state.clear.assert_not_awaited()
    services.ref_code.generate_unique.assert_not_awaited()


async def test_name_entered_happy_path_clears_state_and_shows_payment_screen() -> None:
    message = MagicMock()
    message.text = "Alice Smith"
    message.answer = AsyncMock()

    state = MagicMock()
    state.clear = AsyncMock()
    state.set_state = AsyncMock()

    session = MagicMock()
    # session.refresh(user) is awaited after the writes to pull the
    # updated columns back into the ORM object.
    session.refresh = AsyncMock()

    services = _services()
    container = _container(services)

    await on_name_entered(
        message,
        state=state,
        session=session,
        user=_user(),
        container=container,
    )

    services.user.set_name.assert_awaited_once_with(1, "Alice Smith")
    services.ref_code.generate_unique.assert_awaited_once()
    services.user.attach_reference_code.assert_awaited_once_with(1, "A7F2K9")
    state.clear.assert_awaited_once()
    message.answer.assert_awaited()  # payment screen was sent
