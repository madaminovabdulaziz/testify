"""Onboarding handlers: «Начать» tap → contact share → name capture.

Walks the user from ``status='new'`` to ``status='pending_payment'`` per
PRODUCT_BLUEPRINT §8.1 + §10.1. Validation lives in
:class:`UserService`; this layer just adapts aiogram events to service
calls and renders the next prompt.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.common import _build_payment_screen
from app.bot.keyboards.onboarding import (
    START_ONBOARDING_CALLBACK,
    contact_request_keyboard,
)
from app.bot.states.onboarding import OnboardingState
from app.core.container import Container
from app.exceptions import InvalidNameError
from app.models.user import User
from app.services.reference_code import ReferenceCodeGenerationError

logger = structlog.get_logger()

router = Router(name="onboarding")

_PHONE_PROMPT = (
    "Чтобы продолжить, поделитесь, пожалуйста, своим номером телефона. "
    "Это нужно, чтобы преподаватель могла связаться с вами при необходимости."
)
_PHONE_REMINDER = "Пожалуйста, нажмите кнопку «📱 Поделиться номером» ниже."
_NAME_PROMPT = "Спасибо! Теперь напишите, пожалуйста, ваше полное имя (как в документе)."
_ALREADY_STARTED = "Вы уже начали регистрацию. Напишите /start, чтобы продолжить."


@router.callback_query(F.data == START_ONBOARDING_CALLBACK)
async def on_start_onboarding(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """User tapped «Начать ▶️» — advance them to the contact-share step."""
    await callback.answer()
    if user.status != "new":
        # A non-new user tapped a stale «Начать» button (scrolled up to the
        # old welcome message). Restarting onboarding here would push them
        # back through phone/name capture and overwrite their phone, name
        # and — worst of all — their reference_code, breaking the admin's
        # code↔deposit audit trail (CODE_REVIEW H6). Refuse instead.
        if callback.message is not None:
            await callback.message.answer(_ALREADY_STARTED)
        return
    services = container.services(session)
    await services.user.start_onboarding(user.id)
    await state.set_state(OnboardingState.waiting_for_phone)
    if callback.message is not None:
        await callback.message.answer(
            _PHONE_PROMPT,
            reply_markup=contact_request_keyboard(),
        )


@router.message(
    StateFilter(OnboardingState.waiting_for_phone),
    F.contact,
)
async def on_contact_shared(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """User shared their contact via the keyboard button."""
    contact = message.contact
    assert contact is not None  # F.contact filter guarantees this

    # H3: Telegram lets a user share *anyone's* contact card. We still
    # accept it (some students legitimately have multiple SIMs / share a
    # family member's number), but flag the mismatch for the admin per
    # PRODUCT_BLUEPRINT §8.1.
    sender_id = message.from_user.id if message.from_user else None
    if contact.user_id and sender_id is not None and contact.user_id != sender_id:
        logger.warning(
            "contact_user_mismatch",
            contact_user_id=contact.user_id,
            sender_id=sender_id,
        )

    services = container.services(session)
    await services.user.set_phone(user.id, contact.phone_number)
    await state.set_state(OnboardingState.waiting_for_name)
    await message.answer(_NAME_PROMPT, reply_markup=ReplyKeyboardRemove())


@router.message(StateFilter(OnboardingState.waiting_for_phone))
async def remind_share_contact(message: Message) -> None:
    """Any non-contact message while we're waiting for the contact share."""
    await message.answer(_PHONE_REMINDER, reply_markup=contact_request_keyboard())


@router.message(
    StateFilter(OnboardingState.waiting_for_name),
    F.text,
)
async def on_name_entered(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """User typed their full name — validate, generate reference code, show payment screen."""
    text = message.text or ""
    services = container.services(session)
    try:
        await services.user.set_name(user.id, text)
    except InvalidNameError as exc:
        # Stay in the same state so the user can retry.
        await message.answer(exc.user_message)
        return

    try:
        code = await services.ref_code.generate_unique()
    except ReferenceCodeGenerationError:
        # Astronomically unlikely (≈31^6 codes), but if every retry collides
        # we surface a friendly message instead of a 500 (CODE_REVIEW L3).
        logger.warning("reference_code_generation_failed", user_id=user.id)
        await message.answer("Технические работы. Попробуйте, пожалуйста, чуть позже.")
        return
    await services.user.attach_reference_code(user.id, code)

    # The service issued UPDATEs, but the ``user`` object in the session
    # still holds the pre-update column values. Refresh just this row
    # (a single explicit SELECT) so the payment-screen view sees the
    # freshly-assigned ``reference_code`` and ``status``. NEVER use
    # ``session.expire_all()`` in async handlers — it marks every loaded
    # object stale, and the first read of any attribute triggers a
    # synchronous lazy-load that an ``AsyncSession`` can't service.
    await session.refresh(user)

    await state.clear()
    rendered = await _build_payment_screen(user, services)
    await message.answer(
        rendered.text,
        reply_markup=rendered.reply_markup,
        parse_mode=rendered.parse_mode,
    )
