"""Unit tests for the two new view functions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.bot.views.admin_receipt import render_admin_receipt_notification
from app.bot.views.payment_screen import render_payment_instructions


def _user(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 1,
        "telegram_id": 100,
        "username": "alice",
        "full_name": "Alice Smith",
        "phone": "+998901234567",
        "reference_code": "A7F2K9",
        "status": "pending_payment",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _receipt(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 42,
        "user_id": 1,
        "created_at": datetime(2026, 5, 22, 9, 30, tzinfo=UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------- payment screen ----------


_TEMPLATE = (
    "💰 Сумма: {amount_display}\n"
    "💳 Карта: {card_number}\n"
    "👤 Получатель: {recipient_name}\n"
    "Код: #{reference_code}"
)


def test_payment_screen_substitutes_all_placeholders() -> None:
    rendered = render_payment_instructions(
        _user(),
        instructions_template=_TEMPLATE,
        amount_display="150 000 сум",
        card_number="8600 1234 5678 9012",
        recipient_name="Дильноза опа",
        support_contact="dilnoza",
    )
    assert "150 000 сум" in rendered.text
    assert "8600 1234 5678 9012" in rendered.text
    assert "Дильноза опа" in rendered.text
    assert "#A7F2K9" in rendered.text
    assert rendered.reply_markup is not None  # Я оплатил + У меня вопрос


def test_payment_screen_html_escapes_dynamic_values() -> None:
    rendered = render_payment_instructions(
        _user(),
        instructions_template="{recipient_name}",
        amount_display="",
        card_number="",
        recipient_name="Smith <admin> & Co.",
        support_contact=None,
    )
    # Escaped angle brackets + ampersand.
    assert "&lt;" in rendered.text
    assert "&amp;" in rendered.text
    assert "<admin>" not in rendered.text


def test_payment_screen_omits_support_button_when_unset() -> None:
    rendered = render_payment_instructions(
        _user(),
        instructions_template="X",
        amount_display="",
        card_number="",
        recipient_name="",
        support_contact=None,
    )
    # Only one button (Я оплатил), no support URL button.
    buttons = rendered.reply_markup.inline_keyboard
    assert sum(len(row) for row in buttons) == 1


# ---------- admin receipt notification ----------


def test_admin_caption_includes_user_details_and_code() -> None:
    rendered = render_admin_receipt_notification(_user(), _receipt())
    assert "🧾 Новый чек на проверку" in rendered.text
    assert "Alice Smith" in rendered.text
    assert "+998901234567" in rendered.text
    assert "@alice" in rendered.text
    assert "#A7F2K9" in rendered.text
    # Two inline buttons: ✅ Одобрить + ❌ Отклонить
    buttons = rendered.reply_markup.inline_keyboard
    assert sum(len(row) for row in buttons) == 2


def test_admin_caption_appends_warnings_section() -> None:
    rendered = render_admin_receipt_notification(
        _user(),
        _receipt(),
        warnings=["⚠️ Похожий чек уже был одобрен ранее."],
    )
    assert "⚠️" in rendered.text
    assert "Похожий чек" in rendered.text


def test_admin_caption_handles_missing_optional_fields() -> None:
    user = _user(username=None, full_name=None, phone=None, reference_code=None)
    rendered = render_admin_receipt_notification(user, _receipt())
    # Each "—" placeholder is present where data is missing.
    assert "Имя: —" in rendered.text
    assert "Телефон: —" in rendered.text
    assert "Username: —" in rendered.text
    assert "Код: #—" in rendered.text
