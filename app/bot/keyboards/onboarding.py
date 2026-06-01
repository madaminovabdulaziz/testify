"""Keyboards for the welcome + contact-share onboarding steps."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.i18n import BTN_SHARE_PHONE, BTN_START_ONBOARDING

# Plain string used as the callback_data for the welcome "Начать ▶️" button.
START_ONBOARDING_CALLBACK = "start_onboarding"


def welcome_keyboard() -> InlineKeyboardMarkup:
    """The single «Начать ▶️» button shown on the welcome message."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_START_ONBOARDING, callback_data=START_ONBOARDING_CALLBACK)
    return builder.as_markup()


def contact_request_keyboard() -> ReplyKeyboardMarkup:
    """Reply keyboard with a single ``request_contact=True`` button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SHARE_PHONE, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
