"""On-boot reconciliation: re-register attempt jobs + finalize the strays.

ARCHITECTURE_SPEC §11.3. Two responsibilities, both critical on a
cold start:

1. **Re-register timer jobs** for every attempt still ``in_progress`` in
   the DB. The Redis jobstore *should* survive a restart, but if it was
   flushed or never persisted to disk we can't trust it; re-issuing the
   ``add_job`` with ``replace_existing=True`` is idempotent (no-op when
   the job already exists with the same id, otherwise re-creates it).

2. **Sweep already-expired in-flight attempts** — any attempt whose
   ``expires_at < now()`` while ``status='in_progress'`` was missed by
   the scheduler (because run_date was in the past, APScheduler skips
   silently). Finalize them now via
   :meth:`AttemptService.finish(reason='expired')`, which is itself
   idempotent (status-guarded UPDATE) and which sends the user the
   result via the queued bot.send_message path.

Runs once, synchronously, **before** the bot starts accepting updates.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import structlog

from app.jobs.registry import schedule_attempt_jobs
from app.utils.datetime import now_utc

if TYPE_CHECKING:
    from app.core.container import Container

logger = structlog.get_logger()


async def reconcile_attempts(container: Container) -> None:
    """Re-register jobs for in-progress attempts and finish overdue ones.

    Idempotent — safe to invoke more than once. The expire path of an
    already-finished attempt no-ops because ``finish()`` is guarded by
    ``status='in_progress'``.
    """
    async with container.session_factory() as session:
        services = container.services(session)
        in_progress = await services.attempt.list_in_progress()
        await session.commit()

    rescheduled = 0
    finished_immediately = 0
    for attempt in in_progress:
        schedule_attempt_jobs(container.scheduler, attempt)
        rescheduled += 1
        # ``schedule_attempt_jobs`` silently skips any slot whose run_at
        # is already in the past. If the attempt's ``expires_at`` itself
        # is past now, *nothing* was scheduled for it (including the
        # expire slot) — we have to finalize it ourselves right now.
        if _is_overdue(attempt.expires_at):
            await _finalize_overdue(attempt.id)
            finished_immediately += 1

    logger.info(
        "startup_reconciliation_done",
        rescheduled=rescheduled,
        finalized_immediately=finished_immediately,
    )


async def _finalize_overdue(attempt_id: int) -> None:
    """Best-effort: call ``attempt_expire_job`` for a missed attempt.

    Errors are logged + swallowed so one bad row doesn't abort the
    whole reconciliation sweep at boot.
    """
    # Local import: avoids a module-level circular dep on the timer
    # module, which itself imports the runtime container.
    from app.jobs.attempt_timer import attempt_expire_job

    try:
        await attempt_expire_job(attempt_id=attempt_id)
    except Exception:
        logger.exception("startup_reconciliation_finalize_failed", attempt_id=attempt_id)


def _is_overdue(expires_at: datetime) -> bool:
    """True iff the attempt's expiry has already passed."""
    return expires_at <= now_utc()
