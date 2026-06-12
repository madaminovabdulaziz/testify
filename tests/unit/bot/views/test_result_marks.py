"""Unit tests for the per-question ✅/❌ marks block on the result screens."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.bot.views.result_screen import render_prior_result_screen, render_result_screen
from app.services.scoring_service import SectionScores


def _attempt() -> SimpleNamespace:
    return SimpleNamespace(
        started_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 6, 12, 10, 30, tzinfo=UTC),
    )


def _scores() -> SectionScores:
    return SectionScores(total=47, rus_tili=34, pedagogik=9, kasbiy=4)


def _full_marks(**overrides: bool | None) -> dict[int, bool | None]:
    marks: dict[int, bool | None] = dict.fromkeys(range(1, 51), True)
    for key, value in overrides.items():
        marks[int(key.removeprefix("q"))] = value
    return marks


def test_result_screen_shows_marks_grid_and_summaries() -> None:
    marks = _full_marks(q3=False, q38=False, q19=None)

    rendered = render_result_screen(_attempt(), _scores(), group_invite_link=None, marks=marks)

    assert "📚 Русский язык" in rendered.text
    assert "👨‍🏫 Педагогическое мастерство" in rendered.text
    assert "📋 Профессиональный стандарт" in rendered.text
    assert "3❌" in rendered.text
    assert "38❌" in rendered.text
    assert "19⬜" in rendered.text
    assert "1✅" in rendered.text
    assert "❌ Ошибки: 3, 38" in rendered.text
    assert "⬜ Без ответа: 19" in rendered.text
    # The teaching hook stays.
    assert "Разбор вопросов — в чате студентов." in rendered.text


def test_result_screen_without_marks_renders_as_before() -> None:
    rendered = render_result_screen(_attempt(), _scores(), group_invite_link=None)

    assert "Ошибки" not in rendered.text
    assert "📚 Русский язык" not in rendered.text
    assert "Ваш результат: 47/50" in rendered.text


def test_all_correct_omits_error_and_unanswered_lines() -> None:
    rendered = render_result_screen(
        _attempt(), _scores(), group_invite_link=None, marks=_full_marks()
    )

    assert "Ошибки" not in rendered.text
    assert "Без ответа" not in rendered.text
    assert "50✅" in rendered.text


def test_grid_rows_capped_at_ten_marks() -> None:
    rendered = render_result_screen(
        _attempt(), _scores(), group_invite_link=None, marks=_full_marks()
    )
    grid_lines = [ln for ln in rendered.text.split("\n") if ln.startswith("1✅")]
    assert grid_lines, "first grid row missing"
    assert len(grid_lines[0].split(" ")) == 10


def test_prior_result_screen_includes_marks() -> None:
    marks = _full_marks(q7=False)

    rendered = render_prior_result_screen(
        _attempt(), _scores(), group_invite_link=None, marks=marks
    )

    assert "Вы уже проходили этот тест." in rendered.text
    assert "❌ Ошибки: 7" in rendered.text


def test_message_stays_within_telegram_limit() -> None:
    # Worst case: everything wrong → longest «Ошибки» line.
    marks: dict[int, bool | None] = dict.fromkeys(range(1, 51), False)
    rendered = render_result_screen(_attempt(), _scores(), group_invite_link=None, marks=marks)
    assert len(rendered.text) < 4096
