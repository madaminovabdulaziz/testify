"""Unit tests for the jobs registry — scheduling, cancelling, recurring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from apscheduler.jobstores.base import JobLookupError

from app.jobs.registry import (
    attempt_job_id,
    cancel_attempt_jobs,
    register_recurring_jobs,
    schedule_attempt_jobs,
)


def _attempt(*, expires_at: datetime, attempt_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(id=attempt_id, expires_at=expires_at)


def test_attempt_job_id_is_deterministic() -> None:
    assert attempt_job_id(42, "warn_10min") == "attempt_warn_10min:42"
    assert attempt_job_id(7, "expire") == "attempt_expire:7"


def test_schedule_attempt_jobs_adds_four_jobs_when_all_future() -> None:
    scheduler = MagicMock()
    future = datetime(2099, 1, 1, tzinfo=UTC)  # all slots in the far future
    schedule_attempt_jobs(scheduler, _attempt(expires_at=future))
    assert scheduler.add_job.call_count == 4
    job_ids = {call.kwargs["id"] for call in scheduler.add_job.call_args_list}
    assert job_ids == {
        "attempt_warn_10min:42",
        "attempt_warn_5min:42",
        "attempt_warn_1min:42",
        "attempt_expire:42",
    }
    for call in scheduler.add_job.call_args_list:
        assert call.kwargs["replace_existing"] is True
        assert call.kwargs["kwargs"] == {"attempt_id": 42}


def test_schedule_attempt_jobs_skips_past_slots() -> None:
    """If the attempt has only 2 minutes left, only warn_1min + expire are added."""
    scheduler = MagicMock()
    expires_at = datetime.now(UTC) + timedelta(seconds=120)
    schedule_attempt_jobs(scheduler, _attempt(expires_at=expires_at))
    # warn_10min (T-600s) and warn_5min (T-300s) are both in the past
    # relative to expires_at -- their run_at is before now().
    assert scheduler.add_job.call_count == 2
    job_ids = {call.kwargs["id"] for call in scheduler.add_job.call_args_list}
    assert job_ids == {"attempt_warn_1min:42", "attempt_expire:42"}


def test_schedule_attempt_jobs_skips_everything_when_already_expired() -> None:
    """An overdue attempt schedules nothing — the reconciliation sweep finalizes it."""
    scheduler = MagicMock()
    expires_at = datetime.now(UTC) - timedelta(seconds=60)
    schedule_attempt_jobs(scheduler, _attempt(expires_at=expires_at))
    scheduler.add_job.assert_not_called()


def test_cancel_attempt_jobs_removes_all_four_suffixes() -> None:
    scheduler = MagicMock()
    cancel_attempt_jobs(scheduler, attempt_id=42)
    removed_ids = [call.args[0] for call in scheduler.remove_job.call_args_list]
    assert set(removed_ids) == {
        "attempt_warn_10min:42",
        "attempt_warn_5min:42",
        "attempt_warn_1min:42",
        "attempt_expire:42",
    }


def test_cancel_attempt_jobs_swallows_missing_jobs() -> None:
    """JobLookupError is expected (warning already fired, etc.) — don't propagate."""
    scheduler = MagicMock()
    scheduler.remove_job.side_effect = JobLookupError("not found")
    cancel_attempt_jobs(scheduler, attempt_id=42)  # must not raise
    assert scheduler.remove_job.call_count == 4


def test_register_recurring_jobs_adds_reminder_and_expired_sweep() -> None:
    scheduler = MagicMock()
    register_recurring_jobs(scheduler)
    # Both recurring jobs: the hourly pending-receipt reminder and the
    # per-minute expired-attempt safety-net sweep (CODE_REVIEW C4).
    assert scheduler.add_job.call_count == 2
    registered = {call.kwargs["id"]: call for call in scheduler.add_job.call_args_list}
    assert set(registered) == {"pending_receipt_reminder", "expired_attempt_sweep"}
    for call in registered.values():
        assert call.kwargs["replace_existing"] is True
