"""Unit tests for the admin operations commands."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.admin.operations import (
    cmd_attempt,
    cmd_ban,
    cmd_find,
    cmd_leaderboard,
    cmd_stats,
    cmd_tests,
    cmd_unban,
)
from app.repositories.attempt_repository import LeaderboardEntry
from app.repositories.test_repository import TestListEntry
from app.services.attempt_service import AttemptDetail
from app.services.stats_service import StatsSnapshot


def _admin_user() -> SimpleNamespace:
    return SimpleNamespace(id=99)


def _command(args: str | None = "") -> SimpleNamespace:
    return SimpleNamespace(args=args)


def _message(*, from_id: int = 900) -> MagicMock:
    msg = MagicMock()
    msg.answer = AsyncMock()
    msg.from_user = SimpleNamespace(id=from_id, username="admin")
    return msg


def _container(services: MagicMock) -> MagicMock:
    container = MagicMock()
    container.services = MagicMock(return_value=services)
    return container


def _user(**overrides) -> SimpleNamespace:
    base = {
        "id": 7,
        "telegram_id": 12345,
        "username": "alice",
        "full_name": "Alice Smith",
        "phone": "+998901234567",
        "reference_code": "A7F2K9",
        "status": "approved",
        "bot_blocked": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ============================================================
# /stats
# ============================================================


async def test_stats_renders_each_status_bucket() -> None:
    services = MagicMock()
    services.stats.snapshot = AsyncMock(
        return_value=StatsSnapshot(
            total_users=42,
            users_by_status={"approved": 30, "pending_approval": 5},
            receipts_by_status={"pending": 5, "approved": 30, "rejected": 2},
            tests_by_status={"active": 1, "archived": 9, "draft": 0},
            attempts_by_status={"in_progress": 4, "submitted": 90, "expired": 10},
        )
    )
    container = _container(services)
    message = _message()

    await cmd_stats(
        message,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    text = message.answer.await_args.args[0]
    assert "42" in text  # total users
    assert "30" in text  # approved
    assert "5" in text  # pending
    assert "100" in text  # finished attempts (90+10)


# ============================================================
# /tests
# ============================================================


async def test_tests_lists_recent_with_ids() -> None:
    services = MagicMock()
    services.test.list_recent = AsyncMock(
        return_value=[
            TestListEntry(
                id=5,
                title="Тест от 2026-06-09",
                status="active",
                question_count=50,
                attempt_count=23,
                published_at=datetime(2026, 6, 9, 8, 0, tzinfo=UTC),
            ),
        ]
    )
    container = _container(services)
    message = _message()

    await cmd_tests(
        message,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.test.list_recent.assert_awaited_once_with(limit=15)
    text = message.answer.await_args.args[0]
    assert "#5" in text
    assert "активный" in text


async def test_tests_empty() -> None:
    services = MagicMock()
    services.test.list_recent = AsyncMock(return_value=[])
    container = _container(services)
    message = _message()

    await cmd_tests(
        message,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    assert "нет ни одного теста" in message.answer.await_args.args[0].lower()


# ============================================================
# /find
# ============================================================


async def test_find_requires_argument() -> None:
    services = MagicMock()
    services.user.find = AsyncMock()
    container = _container(services)
    message = _message()

    await cmd_find(
        message,
        command=_command(args=None),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.user.find.assert_not_awaited()
    assert "Использование" in message.answer.await_args.args[0]


async def test_find_unknown_user() -> None:
    services = MagicMock()
    services.user.find = AsyncMock(return_value=None)
    container = _container(services)
    message = _message()

    await cmd_find(
        message,
        command=_command("nobody"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.user.find.assert_awaited_once_with("nobody")
    assert "не найден" in message.answer.await_args.args[0].lower()


async def test_find_renders_user_card_with_pending_count() -> None:
    services = MagicMock()
    services.user.find = AsyncMock(return_value=_user())
    services.receipt.count_pending_for_user = AsyncMock(return_value=2)
    container = _container(services)
    message = _message()

    await cmd_find(
        message,
        command=_command("+998901234567"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    text = message.answer.await_args.args[0]
    assert "Alice Smith" in text
    assert "@alice" in text
    assert "#A7F2K9" in text
    assert "approved" in text
    assert "чеки на проверке: 2" in text


# ============================================================
# /ban  +  /unban
# ============================================================


async def test_ban_rejects_non_integer_arg() -> None:
    services = MagicMock()
    services.user.ban = AsyncMock()
    container = _container(services)
    message = _message()

    await cmd_ban(
        message,
        command=_command("not-a-number"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.user.ban.assert_not_awaited()
    assert "Использование" in message.answer.await_args.args[0]


async def test_ban_marks_user_when_found() -> None:
    services = MagicMock()
    services.user.get_user = AsyncMock(return_value=_user())
    services.user.ban = AsyncMock()
    services.attempt.finalize_in_progress_for_user = AsyncMock(return_value=1)
    container = _container(services)
    message = _message()

    await cmd_ban(
        message,
        command=_command("7"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.user.ban.assert_awaited_once_with(7)
    # Ban also force-expires the user's open attempt + cancels its jobs (H20).
    services.attempt.finalize_in_progress_for_user.assert_awaited_once_with(7)
    assert "забанен" in message.answer.await_args.args[0]


async def test_unban_skips_when_user_not_in_banned_status() -> None:
    services = MagicMock()
    services.user.get_user = AsyncMock(return_value=_user(status="approved"))
    services.user.unban = AsyncMock()
    container = _container(services)
    message = _message()

    await cmd_unban(
        message,
        command=_command("7"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.user.unban.assert_not_awaited()
    assert "не в бане" in message.answer.await_args.args[0]


async def test_unban_calls_service_for_banned_user() -> None:
    services = MagicMock()
    services.user.get_user = AsyncMock(return_value=_user(status="banned"))
    services.user.unban = AsyncMock()
    container = _container(services)
    message = _message()

    await cmd_unban(
        message,
        command=_command("7"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.user.unban.assert_awaited_once_with(7)
    assert "восстановлен" in message.answer.await_args.args[0]


# ============================================================
# /leaderboard
# ============================================================


async def test_leaderboard_empty_renders_friendly_message() -> None:
    services = MagicMock()
    services.test.get_test = AsyncMock(
        return_value=SimpleNamespace(id=3, title="Тест от 2026-05-24")
    )
    services.attempt.list_top_for_test = AsyncMock(return_value=[])
    container = _container(services)
    message = _message()

    await cmd_leaderboard(
        message,
        command=_command("3"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    text = message.answer.await_args.args[0]
    assert "нет завершённых попыток" in text


async def test_leaderboard_renders_ranked_entries() -> None:
    services = MagicMock()
    services.test.get_test = AsyncMock(return_value=SimpleNamespace(id=3, title="Тест"))
    services.attempt.list_top_for_test = AsyncMock(
        return_value=[
            LeaderboardEntry(
                attempt_id=1,
                user_id=10,
                full_name="Alice",
                score_total_correct=48,
                finished_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
            ),
            LeaderboardEntry(
                attempt_id=2,
                user_id=11,
                full_name=None,
                score_total_correct=45,
                finished_at=datetime(2026, 5, 24, 11, 0, tzinfo=UTC),
            ),
        ]
    )
    container = _container(services)
    message = _message()

    await cmd_leaderboard(
        message,
        command=_command("3"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    text = message.answer.await_args.args[0]
    assert "Alice" in text
    assert "48" in text
    assert "user_11" in text  # fallback when full_name is None


# ============================================================
# /attempt
# ============================================================


async def test_attempt_not_found() -> None:
    services = MagicMock()
    services.attempt.get_attempt_detail = AsyncMock(return_value=None)
    container = _container(services)
    message = _message()

    await cmd_attempt(
        message,
        command=_command("999"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    assert "Попытка не найдена" in message.answer.await_args.args[0]


async def test_attempt_renders_detail_with_per_question_table() -> None:
    questions = (
        SimpleNamespace(
            id=1,
            position=1,
            section="rus_tili",
            correct_option="A",
            question_text="Q1",
            option_a="a",
            option_b="b",
            option_c="c",
            option_d="d",
        ),
        SimpleNamespace(
            id=2,
            position=2,
            section="rus_tili",
            correct_option="B",
            question_text="Q2",
            option_a="a",
            option_b="b",
            option_c="c",
            option_d="d",
        ),
    )
    answers = {
        1: SimpleNamespace(question_id=1, selected_option="A", is_correct=True),
        # Question 2 unanswered.
    }
    attempt = SimpleNamespace(
        id=42,
        user_id=7,
        test_id=3,
        status="submitted",
        started_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 5, 24, 10, 30, tzinfo=UTC),
        score_total_correct=1,
    )
    services = MagicMock()
    services.attempt.get_attempt_detail = AsyncMock(
        return_value=AttemptDetail(
            attempt=attempt, questions=questions, answers_by_question_id=answers
        )
    )
    services.user.get_user = AsyncMock(return_value=_user())
    services.test.get_test = AsyncMock(return_value=SimpleNamespace(id=3, title="Тест"))
    container = _container(services)
    message = _message()

    await cmd_attempt(
        message,
        command=_command("42"),
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    text = message.answer.await_args.args[0]
    assert "Попытка #42" in text
    assert "submitted" in text
    assert "Alice Smith" in text
    assert "Тест" in text
    assert "1/50" in text
