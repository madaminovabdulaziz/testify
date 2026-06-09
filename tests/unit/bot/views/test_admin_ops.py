"""Unit tests for the pure admin-ops view functions touched by the
id-discovery feature: render_test_list + the leaderboard attempt-id column."""

from __future__ import annotations

from datetime import UTC, datetime

from app.bot.views.admin_ops import render_leaderboard, render_test_list
from app.repositories.attempt_repository import LeaderboardEntry
from app.repositories.test_repository import TestListEntry


def _entry(**overrides: object) -> TestListEntry:
    base = {
        "id": 5,
        "title": "Тест от 2026-06-09",
        "status": "active",
        "question_count": 50,
        "attempt_count": 23,
        "published_at": datetime(2026, 6, 9, 8, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return TestListEntry(**base)  # type: ignore[arg-type]


# ============================================================
# render_test_list
# ============================================================


def test_test_list_empty_prompts_to_upload() -> None:
    text = render_test_list([])
    assert "нет ни одного теста" in text.lower()


def test_test_list_surfaces_id_status_and_counts() -> None:
    text = render_test_list([_entry()])
    assert "#5" in text  # the test_id — the whole point
    assert "активный" in text
    assert "вопросов: 50" in text
    assert "попыток: 23" in text
    # Chaining hint to the next discovery step.
    assert "/leaderboard" in text
    assert "/attempt" in text


def test_test_list_marks_each_status() -> None:
    text = render_test_list(
        [
            _entry(id=5, status="active"),
            _entry(id=4, status="archived"),
            _entry(id=3, status="draft"),
        ]
    )
    assert "активный" in text
    assert "архив" in text
    assert "черновик" in text


def test_test_list_escapes_title() -> None:
    text = render_test_list([_entry(title="<b>x</b>")])
    assert "<b>x</b>" not in text
    assert "&lt;b&gt;" in text


# ============================================================
# render_leaderboard — attempt-id column
# ============================================================


def _lb(**overrides: object) -> LeaderboardEntry:
    base = {
        "attempt_id": 123,
        "user_id": 10,
        "full_name": "Alice",
        "score_total_correct": 48,
        "finished_at": datetime(2026, 6, 9, 10, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return LeaderboardEntry(**base)  # type: ignore[arg-type]


def test_leaderboard_shows_attempt_id_and_chain_hint() -> None:
    text = render_leaderboard(test_title="Тест", entries=[_lb(attempt_id=123)])
    assert "123" in text  # attempt_id, so admin can run /attempt 123
    assert "попытка" in text  # column header
    assert "/attempt" in text  # chaining hint


def test_leaderboard_falls_back_to_user_id_when_no_name() -> None:
    text = render_leaderboard(test_title="Тест", entries=[_lb(full_name=None, user_id=11)])
    assert "user_11" in text


def test_leaderboard_empty_unchanged() -> None:
    text = render_leaderboard(test_title="Тест", entries=[])
    assert "нет завершённых попыток" in text
