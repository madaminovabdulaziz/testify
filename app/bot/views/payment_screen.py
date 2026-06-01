"""Render the payment-instructions screen (PRODUCT_BLUEPRINT §8.2 + §11.2).

Pure function. The handler fetches the live values from
:class:`SettingsService` and hands them in.
"""

from __future__ import annotations

from app.bot.keyboards.payment import payment_buttons_keyboard
from app.bot.views import RenderedMessage
from app.models.user import User
from app.utils.text import html_escape, safe_format


def render_payment_instructions(
    user: User,
    *,
    instructions_template: str,
    amount_display: str,
    card_number: str,
    recipient_name: str,
    support_contact: str | None,
) -> RenderedMessage:
    """Substitute the four placeholders into the seeded ``payment_instructions`` template.

    Everything is HTML-escaped: even though the values are
    admin-controlled (via ``/set``), the admin might type characters
    like ``&`` or ``<`` and the bot sends with ``parse_mode=HTML``.
    """
    # safe_format (not str.format) so an admin who typos a placeholder via
    # /set — e.g. "{whoops}" — doesn't KeyError on every onboarding user
    # from then on (CODE_REVIEW H17).
    text = safe_format(
        instructions_template,
        {
            "amount_display": html_escape(amount_display),
            "card_number": html_escape(card_number),
            "recipient_name": html_escape(recipient_name),
            "reference_code": html_escape(user.reference_code or ""),
        },
    )
    return RenderedMessage(
        text=text,
        reply_markup=payment_buttons_keyboard(support_contact),
    )
