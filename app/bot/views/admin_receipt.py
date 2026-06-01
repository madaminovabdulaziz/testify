"""Render the admin-group receipt notification (PRODUCT_BLUEPRINT §8.3 step 1)."""

from __future__ import annotations

from collections.abc import Iterable

from app.bot.keyboards.admin import receipt_review_keyboard
from app.bot.views import RenderedMessage
from app.models.receipt import PaymentReceipt
from app.models.user import User
from app.utils.datetime import format_timestamp_local
from app.utils.text import html_escape


def render_admin_receipt_notification(
    user: User,
    receipt: PaymentReceipt,
    *,
    warnings: Iterable[str] | None = None,
) -> RenderedMessage:
    """Build the admin-group caption + ✅/❌ inline buttons for a new receipt."""
    full_name = html_escape(user.full_name) if user.full_name else "—"
    phone = html_escape(user.phone) if user.phone else "—"
    username_disp = f"@{html_escape(user.username)}" if user.username else "—"
    code_disp = html_escape(user.reference_code) if user.reference_code else "—"
    timestamp = format_timestamp_local(receipt.created_at)

    lines: list[str] = [
        "🧾 Новый чек на проверку",
        "",
        f"👤 Имя: {full_name}",
        f"📱 Телефон: {phone}",
        f"🆔 Username: {username_disp}",
        f"🔖 Код: #{code_disp}",
        f"⏱ Отправлен: {timestamp}",
    ]
    warning_list = list(warnings or [])
    if warning_list:
        lines.append("")
        lines.extend(html_escape(w) for w in warning_list)

    return RenderedMessage(
        text="\n".join(lines),
        reply_markup=receipt_review_keyboard(receipt.id),
    )
