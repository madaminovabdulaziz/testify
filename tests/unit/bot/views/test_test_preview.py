"""Unit tests for the test-preview view + parse-error formatter."""

from __future__ import annotations

from types import SimpleNamespace

from app.bot.views.test_preview import (
    render_image_request,
    render_parse_errors,
    render_test_preview,
)


def _test(*, id: int = 42, title: str = "Тест от 2026-05-23") -> SimpleNamespace:
    return SimpleNamespace(id=id, title=title)


# ---------- preview ----------


def test_preview_shows_section_counts_and_title() -> None:
    rendered = render_test_preview(_test(title="Тест от 2026-05-23"))
    assert "📋 Загружен новый тест" in rendered.text
    assert "Всего вопросов: 50" in rendered.text
    assert "Русский язык: 35" in rendered.text
    assert "Педагогическое мастерство: 10" in rendered.text
    assert "Профессиональный стандарт: 5" in rendered.text
    assert "Тест от 2026-05-23" in rendered.text
    # Three publish/cancel buttons in three rows.
    buttons = rendered.reply_markup.inline_keyboard
    assert sum(len(row) for row in buttons) == 3


def test_preview_html_escapes_admin_title() -> None:
    rendered = render_test_preview(_test(title="Title <fix> & retry"))
    assert "&lt;fix&gt;" in rendered.text
    assert "&amp;" in rendered.text


def test_preview_omits_image_line_when_no_images() -> None:
    rendered = render_test_preview(_test())
    assert "изображени" not in rendered.text.lower()


def test_preview_shows_image_count_when_present() -> None:
    rendered = render_test_preview(_test(), image_count=3)
    assert "С изображениями: 3" in rendered.text


# ---------- image request prompt ----------


def test_image_request_first_prompt_lists_pending_and_next() -> None:
    rendered = render_image_request(7, [3, 19, 42])
    assert "3, 19, 42" in rendered.text
    assert "вопроса <b>3</b>" in rendered.text
    # Cancel button reuses the publish-cancel callback for draft 7.
    cbs = [b.callback_data for row in rendered.reply_markup.inline_keyboard for b in row]
    assert any("pub" in cb and "7" in cb for cb in cbs)


def test_image_request_acknowledges_saved_position() -> None:
    rendered = render_image_request(7, [19, 42], saved_position=3)
    assert "вопроса 3 сохранено" in rendered.text
    assert "вопроса <b>19</b>" in rendered.text


# ---------- parse errors ----------


def test_parse_errors_header_present() -> None:
    formatted = render_parse_errors([(2, "пустой текст вопроса")])
    assert formatted.startswith("❌ Не удалось загрузить тест:")


def test_parse_errors_split_file_level_first() -> None:
    formatted = render_parse_errors(
        [
            (12, "пустой текст вопроса"),
            (0, "Ожидалось 50 вопросов, найдено 49."),
            (27, "значение 'correct_option' должно быть A, B, C или D"),
        ]
    )
    lines = formatted.splitlines()
    # Header first.
    assert lines[0].startswith("❌")
    # File-level (line == 0) comes before any "Строка N" line.
    first_row_idx = next(i for i, ln in enumerate(lines) if ln.startswith("• Строка"))
    file_level_idx = next(i for i, ln in enumerate(lines) if "Ожидалось 50" in ln)
    assert file_level_idx < first_row_idx


def test_parse_errors_html_escapes_message_content() -> None:
    formatted = render_parse_errors([(7, "bad <tag>")])
    assert "&lt;tag&gt;" in formatted
    assert "<tag>" not in formatted
