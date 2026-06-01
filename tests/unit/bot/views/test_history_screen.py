"""Unit tests for the «📜 Мои результаты» history view."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.bot.views.history_screen import render_history_screen


def _attempt(
    *,
    status: str = "submitted",
    score: int = 40,
    finished_at: datetime | None = datetime(2026, 5, 24, 10, 30, tzinfo=UTC),
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        score_total_correct=score,
        finished_at=finished_at,
    )


def _test(*, title: str = "Тест от 2026-05-24") -> SimpleNamespace:
    return SimpleNamespace(title=title)


def test_empty_history_shows_friendly_empty_state() -> None:
    text = render_history_screen([])
    assert "пока нет завершённых попыток" in text
    assert "Пройти тест" in text


def test_history_renders_each_entry_with_score_and_percent() -> None:
    text = render_history_screen(
        [
            (_attempt(score=42), _test(title="Тест A")),
            (_attempt(score=30), _test(title="Тест B")),
        ]
    )
    assert "Тест A" in text
    assert "Тест B" in text
    assert "42/50" in text
    assert "30/50" in text
    assert "84.0%" in text
    assert "60.0%" in text


def test_expired_attempt_uses_clock_emoji() -> None:
    text = render_history_screen([(_attempt(status="expired", score=5), _test(title="Авто"))])
    assert "⏰" in text
    assert "5/50" in text


def test_submitted_attempt_uses_check_emoji() -> None:
    text = render_history_screen([(_attempt(status="submitted", score=50), _test(title="Полный"))])
    assert "✅" in text


def test_history_html_escapes_test_title() -> None:
    text = render_history_screen([(_attempt(), _test(title="<script>x</script>"))])
    assert "&lt;script&gt;" in text
    assert "<script>" not in text


def test_history_handles_missing_finished_at() -> None:
    text = render_history_screen([(_attempt(finished_at=None), _test())])
    assert "—" in text  # the placeholder where the timestamp would be
