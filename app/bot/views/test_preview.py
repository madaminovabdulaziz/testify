"""Render the draft-test preview the admin sees after a successful upload.

PRODUCT_BLUEPRINT §8.4: shows section counts (always 35/10/5 since the
parser enforced it) + the auto-generated title, with publish/cancel
inline buttons.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.bot.keyboards.common import cancel_upload_keyboard, publish_buttons_keyboard
from app.bot.views import RenderedMessage
from app.models.test import Test
from app.utils.text import html_escape


def render_test_preview(test: Test, *, image_count: int = 0) -> RenderedMessage:
    """Render the §8.4 preview message + the three publish/cancel buttons.

    ``image_count`` (when > 0) surfaces a confirmation that every illustration
    is in place before the teacher publishes.
    """
    lines = [
        "📋 Загружен новый тест",
        "",
        "Всего вопросов: 50",
        "  • Русский язык: 35 ✓",
        "  • Педагогическое мастерство: 10 ✓",
        "  • Профессиональный стандарт: 5 ✓",
    ]
    if image_count > 0:
        lines.append(f"  • С изображениями: {image_count} 🖼 ✓")
    lines += ["", f"Название: {html_escape(test.title)}"]
    return RenderedMessage(text="\n".join(lines), reply_markup=publish_buttons_keyboard(test.id))


def render_image_request(
    draft_id: int,
    pending_positions: Sequence[int],
    *,
    saved_position: int | None = None,
) -> RenderedMessage:
    """Prompt the admin for the next question image during upload.

    ``pending_positions`` is the still-missing set (ascending); the next photo
    the admin sends is assigned to the first of them. ``saved_position``, when
    given, acknowledges the one just stored.
    """
    next_pos = pending_positions[0]
    remaining = ", ".join(str(p) for p in pending_positions)
    lines: list[str] = []
    if saved_position is not None:
        lines.append(f"✅ Изображение для вопроса {saved_position} сохранено.")
        lines.append("")
    else:
        lines.append("🖼 Для некоторых вопросов нужны изображения.")
        lines.append("")
    lines.append(f"Осталось добавить: {remaining}.")
    lines.append(f"Отправьте фото для вопроса <b>{next_pos}</b>.")
    return RenderedMessage(text="\n".join(lines), reply_markup=cancel_upload_keyboard(draft_id))


def render_parse_errors(errors: Iterable[tuple[int, str]]) -> str:
    """Format TestParseError contents as a single Russian admin-facing message.

    Errors at ``line == 0`` are file-level (e.g. wrong row count, missing
    sheet, wrong section count); they're emitted before the per-row
    entries to give the admin the big picture first.
    """
    file_level: list[str] = []
    row_level: list[str] = []
    for line, message in errors:
        msg = html_escape(message)
        if line == 0:
            file_level.append(f"• {msg}")
        else:
            row_level.append(f"• Строка {line}: {msg}")

    lines = ["❌ Не удалось загрузить тест:"]
    if file_level:
        lines.extend(file_level)
    if row_level:
        if file_level:
            lines.append("")  # blank line between sections
        lines.extend(row_level)
    return "\n".join(lines)
