"""Unit tests for the attempt timer jobs (warnings + auto-expire)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.jobs.attempt_timer import (
    attempt_expire_job,
    attempt_warn_1min_job,
    attempt_warn_5min_job,
    attempt_warn_10min_job,
    expired_attempt_sweep_job,
)
from app.services.attempt_service import AttemptResult
from app.services.scoring_service import SectionScores

# ---------- shared fixtures ----------


def _session_mock() -> MagicMock:
    """A MagicMock that behaves like an AsyncSession context manager."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _session_factory(session: MagicMock) -> MagicMock:
    """Factory mock that returns an ``async with`` context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=cm)
    return factory


def _attempt_row(**overrides) -> SimpleNamespace:
    base = {
        "id": 42,
        "user_id": 7,
        "test_id": 3,
        "status": "in_progress",
        "current_position": 1,
        "started_at": datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
        "finished_at": None,
        "expires_at": datetime(2026, 5, 24, 10, 53, 20, tzinfo=UTC),
        "score_total_correct": None,
        "score_rus_tili_correct": None,
        "score_pedagogik_correct": None,
        "score_kasbiy_correct": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


_UNSET = object()


def _services_mock(
    *,
    claim_returns: object = _UNSET,
    user_returns: object = _UNSET,
    settings_values: dict[str, str] | None = None,
    finish_returns: AttemptResult | None = None,
) -> MagicMock:
    resolved_claim = _attempt_row() if claim_returns is _UNSET else claim_returns
    resolved_user = (
        SimpleNamespace(id=7, telegram_id=12345, status="approved")
        if user_returns is _UNSET
        else user_returns
    )

    services = MagicMock()
    services.attempt.claim_warning_slot = AsyncMock(return_value=resolved_claim)
    services.attempt.get_attempt = AsyncMock(return_value=resolved_claim)
    services.attempt.finish = AsyncMock(return_value=finish_returns)
    services.user.get_user = AsyncMock(return_value=resolved_user)

    settings_values = settings_values or {}

    async def fake_setting(key: str) -> str | None:
        return settings_values.get(key)

    services.settings.get = AsyncMock(side_effect=fake_setting)
    services.notification.send_time_warning = AsyncMock()
    return services


def _container_mock(services: MagicMock, session: MagicMock) -> MagicMock:
    container = MagicMock()
    container.services = MagicMock(return_value=services)
    container.session_factory = _session_factory(session)
    container.bot = MagicMock()
    container.bot.send_message = AsyncMock()
    return container


# ============================================================
# Warning jobs
# ============================================================


@pytest.mark.parametrize(
    "job_fn, slot, settings_key, fallback_marker",
    [
        (attempt_warn_10min_job, "10min", "msg_warning_10min", "10"),
        (attempt_warn_5min_job, "5min", "msg_warning_5min", "5"),
        (attempt_warn_1min_job, "1min", "msg_warning_1min", "1"),
    ],
)
async def test_warning_job_claims_slot_and_sends_message(
    job_fn, slot, settings_key, fallback_marker
) -> None:
    session = _session_mock()
    services = _services_mock(
        settings_values={settings_key: f"⏱ Warning {fallback_marker}!"},
    )
    container = _container_mock(services, session)

    with patch(
        "app.jobs.attempt_timer.get_runtime_container",
        return_value=container,
    ):
        await job_fn(attempt_id=42)

    services.attempt.claim_warning_slot.assert_awaited_once_with(42, slot)
    services.notification.send_time_warning.assert_awaited_once()
    # The slot claim must commit BEFORE the network send.
    session.commit.assert_awaited_once()
    args = services.notification.send_time_warning.await_args.args
    assert args[0] == 12345
    assert fallback_marker in args[1]


async def test_warning_job_falls_back_when_settings_text_missing() -> None:
    session = _session_mock()
    services = _services_mock(settings_values={})  # no msg_warning_10min row
    container = _container_mock(services, session)

    with patch(
        "app.jobs.attempt_timer.get_runtime_container",
        return_value=container,
    ):
        await attempt_warn_10min_job(attempt_id=42)

    args = services.notification.send_time_warning.await_args.args
    assert "10 минут" in args[1]


async def test_warning_job_skips_when_slot_already_claimed() -> None:
    session = _session_mock()
    services = _services_mock(claim_returns=None)
    container = _container_mock(services, session)

    with patch(
        "app.jobs.attempt_timer.get_runtime_container",
        return_value=container,
    ):
        await attempt_warn_5min_job(attempt_id=42)

    services.notification.send_time_warning.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


async def test_warning_job_skips_dm_for_banned_user() -> None:
    # CODE_REVIEW H20: a user banned mid-test must not get warning DMs even
    # if this job won the race against the ban cleanup.
    session = _session_mock()
    services = _services_mock(
        user_returns=SimpleNamespace(id=7, telegram_id=12345, status="banned"),
    )
    container = _container_mock(services, session)

    with patch(
        "app.jobs.attempt_timer.get_runtime_container",
        return_value=container,
    ):
        await attempt_warn_10min_job(attempt_id=42)

    services.notification.send_time_warning.assert_not_awaited()
    session.commit.assert_awaited_once()  # slot claim kept, no retry


async def test_warning_job_skips_when_user_disappeared() -> None:
    session = _session_mock()
    services = _services_mock(user_returns=None)
    container = _container_mock(services, session)

    with patch(
        "app.jobs.attempt_timer.get_runtime_container",
        return_value=container,
    ):
        await attempt_warn_1min_job(attempt_id=42)

    services.notification.send_time_warning.assert_not_awaited()
    session.rollback.assert_awaited_once()


# ============================================================
# Expire job
# ============================================================


def _finish_result(*, status: str = "expired", owned: bool = True) -> AttemptResult:
    attempt = _attempt_row(
        status=status,
        finished_at=datetime(2026, 5, 24, 10, 53, 20, tzinfo=UTC),
        score_total_correct=12,
        score_rus_tili_correct=10,
        score_pedagogik_correct=2,
        score_kasbiy_correct=0,
    )
    scores = SectionScores(rus_tili=10, pedagogik=2, kasbiy=0, total=12)
    return AttemptResult(attempt=attempt, scores=scores, owned_finalization=owned)


async def test_expire_job_finalizes_attempt_and_sends_result_dm() -> None:
    session = _session_mock()
    services = _services_mock(
        claim_returns=_attempt_row(status="in_progress"),
        finish_returns=_finish_result(),
        settings_values={
            "msg_auto_submitted": "⏰ Время вышло!",
            "group_invite_link": "https://t.me/+abc",
        },
    )
    container = _container_mock(services, session)

    with patch(
        "app.jobs.attempt_timer.get_runtime_container",
        return_value=container,
    ):
        await attempt_expire_job(attempt_id=42)

    services.attempt.finish.assert_awaited_once_with(42, reason="expired")
    session.commit.assert_awaited_once()
    # Both DMs: the "time's up" notice (via NotificationService) and
    # the result screen (direct bot.send_message).
    services.notification.send_time_warning.assert_awaited_once()
    container.bot.send_message.assert_awaited_once()
    args = container.bot.send_message.await_args.args
    assert args[0] == 12345  # telegram_id
    assert "12/50" in args[1]


async def test_expire_job_skips_user_dm_when_attempt_already_finished() -> None:
    """If the user finished manually before the timer fired, don't re-DM the result.

    finish() reports ``owned_finalization=False`` because the manual finish
    already flipped the row; the expire job must stay quiet (CODE_REVIEW H2).
    """
    session = _session_mock()
    services = _services_mock(
        claim_returns=_attempt_row(status="submitted"),
        finish_returns=_finish_result(status="submitted", owned=False),
    )
    container = _container_mock(services, session)

    with patch(
        "app.jobs.attempt_timer.get_runtime_container",
        return_value=container,
    ):
        await attempt_expire_job(attempt_id=42)

    services.attempt.finish.assert_awaited_once_with(42, reason="expired")
    services.notification.send_time_warning.assert_not_awaited()
    container.bot.send_message.assert_not_awaited()


async def test_expire_job_skips_dm_when_manual_finish_won_the_race() -> None:
    """H2 regression: the pre-read still shows ``in_progress``, but a manual
    finish landed before our UPDATE, so finish() reports
    ``owned_finalization=False``. The old snapshot logic would have DM'd a
    duplicate result; the job must now stay quiet and commit the no-op.
    """
    session = _session_mock()
    services = _services_mock(
        claim_returns=_attempt_row(status="in_progress"),  # get_attempt snapshot
        finish_returns=_finish_result(status="submitted", owned=False),
    )
    container = _container_mock(services, session)

    with patch(
        "app.jobs.attempt_timer.get_runtime_container",
        return_value=container,
    ):
        await attempt_expire_job(attempt_id=42)

    services.attempt.finish.assert_awaited_once_with(42, reason="expired")
    services.notification.send_time_warning.assert_not_awaited()
    container.bot.send_message.assert_not_awaited()
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_expire_job_handles_missing_attempt_row() -> None:
    session = _session_mock()
    services = _services_mock(claim_returns=None)  # get_attempt returns None
    services.attempt.get_attempt = AsyncMock(return_value=None)
    container = _container_mock(services, session)

    with patch(
        "app.jobs.attempt_timer.get_runtime_container",
        return_value=container,
    ):
        await attempt_expire_job(attempt_id=999)

    services.attempt.finish.assert_not_awaited()
    session.rollback.assert_awaited_once()


# ============================================================
# Expired-attempt safety-net sweep (CODE_REVIEW C4)
# ============================================================


async def test_sweep_finalizes_each_expired_attempt() -> None:
    session = _session_mock()
    services = _services_mock()
    services.attempt.list_expired_in_progress = AsyncMock(return_value=[11, 22, 33])
    container = _container_mock(services, session)

    seen: list[int] = []

    async def fake_expire(attempt_id: int) -> None:
        seen.append(attempt_id)

    with (
        patch("app.jobs.attempt_timer.get_runtime_container", return_value=container),
        patch("app.jobs.attempt_timer.attempt_expire_job", side_effect=fake_expire) as expire,
    ):
        await expired_attempt_sweep_job()

    assert seen == [11, 22, 33]
    assert expire.await_count == 3


async def test_sweep_is_noop_when_nothing_expired() -> None:
    session = _session_mock()
    services = _services_mock()
    services.attempt.list_expired_in_progress = AsyncMock(return_value=[])
    container = _container_mock(services, session)

    with (
        patch("app.jobs.attempt_timer.get_runtime_container", return_value=container),
        patch("app.jobs.attempt_timer.attempt_expire_job") as expire,
    ):
        await expired_attempt_sweep_job()

    expire.assert_not_awaited()


async def test_sweep_continues_after_one_item_fails() -> None:
    """One bad attempt must not abort finalizing the rest of the batch."""
    session = _session_mock()
    services = _services_mock()
    services.attempt.list_expired_in_progress = AsyncMock(return_value=[1, 2, 3])
    container = _container_mock(services, session)

    async def flaky_expire(attempt_id: int) -> None:
        if attempt_id == 2:
            raise RuntimeError("boom")

    with (
        patch("app.jobs.attempt_timer.get_runtime_container", return_value=container),
        patch("app.jobs.attempt_timer.attempt_expire_job", side_effect=flaky_expire) as expire,
    ):
        await expired_attempt_sweep_job()

    # All three attempted despite #2 raising.
    assert expire.await_count == 3
