"""Centralized scheduling: build APScheduler job IDs, register, cancel.

ARCHITECTURE_SPEC §11. Two distinct concerns:

* **Per-attempt timer jobs** — three warning DMs (T-10min, T-5min,
  T-1min) and the auto-submit at T-0. Registered when an attempt
  starts; cancelled if the user finishes early; **re-registered** on
  bot restart from :mod:`app.jobs.startup_reconciliation`. Job IDs are
  deterministic (``attempt_<suffix>:<attempt_id>``) so ``replace_existing``
  makes every register idempotent and ``remove_job`` finds the right
  one to drop.

* **Recurring jobs** — the hourly pending-receipt-reminder sweep. Owned
  by :func:`register_recurring_jobs`, called once at startup.

This module is the single place where job IDs are minted. ``AttemptService``
delegates here so the IDs stay in lock-step with what the
reconciliation sweep and the cancel path use.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import structlog
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.jobs.attempt_timer import (
    attempt_expire_job,
    attempt_warn_1min_job,
    attempt_warn_5min_job,
    attempt_warn_10min_job,
    expired_attempt_sweep_job,
)
from app.jobs.pending_receipt_reminder import pending_receipt_reminder_job
from app.models.attempt import Attempt
from app.utils.datetime import now_utc

logger = structlog.get_logger()


# (suffix, seconds-before-expires_at, callable). Order matters — earliest
# warning first — but they all run independently once scheduled.
_ATTEMPT_SLOTS: tuple[tuple[str, int, Callable[..., Any]], ...] = (
    ("warn_10min", 600, attempt_warn_10min_job),
    ("warn_5min", 300, attempt_warn_5min_job),
    ("warn_1min", 60, attempt_warn_1min_job),
    ("expire", 0, attempt_expire_job),
)

# Mirrors the suffixes used in :func:`schedule_attempt_jobs` so
# :func:`cancel_attempt_jobs` knows exactly which jobs to drop without
# having to introspect the scheduler.
_ATTEMPT_JOB_SUFFIXES: tuple[str, ...] = tuple(suffix for suffix, _, _ in _ATTEMPT_SLOTS)

# Recurring job IDs. Stable so a restart re-registers the same rows in the
# jobstore via ``replace_existing=True``.
_PENDING_RECEIPT_JOB_ID = "pending_receipt_reminder"
_EXPIRED_SWEEP_JOB_ID = "expired_attempt_sweep"


def attempt_job_id(attempt_id: int, suffix: str) -> str:
    """Build the deterministic job ID for one attempt timer slot."""
    return f"attempt_{suffix}:{attempt_id}"


def schedule_attempt_jobs(scheduler: AsyncIOScheduler, attempt: Attempt) -> None:
    """Register the four timer jobs for one attempt.

    Slots that have already passed are skipped — useful both on bot
    restart (the 10-min warning may be behind us when an attempt was
    started 50 minutes ago) and during reconciliation. The
    auto-submit slot (``expire``) IS skipped if past; the
    reconciliation sweep then finishes that attempt synchronously
    instead of waiting on a scheduler that won't fire.
    """
    now = now_utc()
    for suffix, seconds_before_expiry, fn in _ATTEMPT_SLOTS:
        run_at = attempt.expires_at - timedelta(seconds=seconds_before_expiry)
        if run_at <= now:
            continue
        scheduler.add_job(
            fn,
            trigger=DateTrigger(run_date=run_at),
            id=attempt_job_id(attempt.id, suffix),
            replace_existing=True,
            kwargs={"attempt_id": attempt.id},
        )


def cancel_attempt_jobs(scheduler: AsyncIOScheduler, attempt_id: int) -> None:
    """Best-effort remove every timer job for ``attempt_id``.

    Missing-job lookups are silently swallowed — they're expected when
    a warning has already fired or when finish() races the expire job.
    """
    for suffix in _ATTEMPT_JOB_SUFFIXES:
        try:
            scheduler.remove_job(attempt_job_id(attempt_id, suffix))
        except JobLookupError:
            continue


def register_recurring_jobs(scheduler: AsyncIOScheduler) -> None:
    """Register the cron-driven jobs that run for the lifetime of the process.

    Two jobs (ARCHITECTURE_SPEC §11.2 / §10.15):

    * the once-an-hour pending-receipt reminder sweep;
    * the once-a-minute expired-attempt safety-net sweep — the backstop for
      any lost per-attempt expire job (CODE_REVIEW C4).

    ``replace_existing=True`` keeps the Redis jobstore tidy across restarts —
    each row is overwritten with the same id every boot.
    """
    scheduler.add_job(
        pending_receipt_reminder_job,
        trigger=CronTrigger(minute=0),  # top of every hour
        id=_PENDING_RECEIPT_JOB_ID,
        replace_existing=True,
        kwargs={},
    )
    scheduler.add_job(
        expired_attempt_sweep_job,
        trigger=CronTrigger(second=0),  # once a minute, on the minute
        id=_EXPIRED_SWEEP_JOB_ID,
        replace_existing=True,
        kwargs={},
    )
    logger.info(
        "recurring_jobs_registered",
        job_ids=[_PENDING_RECEIPT_JOB_ID, _EXPIRED_SWEEP_JOB_ID],
    )
