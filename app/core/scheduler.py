"""APScheduler factory.

The scheduler is in-process and persists jobs to Redis DB 1 so a bot
restart does not lose pending warning / expiry firings (see
ARCHITECTURE_SPEC §11.1). A separate Redis DB from FSM keeps the keyspace
tidy and lets you ``FLUSHDB`` one without nuking the other.
"""

from __future__ import annotations

from urllib.parse import urlparse

from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import Settings

_JOBSTORE_DB_INDEX = 1


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    """Build (but do not start) the scheduler.

    Caller starts it after the rest of the app is wired so timer-triggered
    jobs that fire on the first tick have a live bot to talk to.
    """
    parsed = urlparse(str(settings.redis_url))

    jobstore = RedisJobStore(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        db=_JOBSTORE_DB_INDEX,
        password=parsed.password,
        # Env-namespace the keys so a shared Redis doesn't mix dev/staging/prod
        # job rows (CODE_REVIEW L16).
        jobs_key=f"{settings.env}:apscheduler.jobs",
        run_times_key=f"{settings.env}:apscheduler.run_times",
    )

    return AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone="UTC",
        job_defaults={
            # Don't pile up missed runs after a long downtime.
            "coalesce": True,
            "max_instances": 1,
            # Fire jobs that became due during downtime, but only if they
            # are still within five minutes of their scheduled time.
            "misfire_grace_time": 300,
        },
    )
