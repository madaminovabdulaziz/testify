"""Redis client factory.

Two Redis databases are used:

  * DB 0 (the URL the caller passes) — aiogram FSM storage and per-user
    throttle counters.
  * DB 1 — APScheduler jobstore (see :mod:`app.core.scheduler`).

aiogram's ``RedisStorage`` handles its own (de)serialization, so we
deliberately leave ``decode_responses=False`` on the shared client:
bytes in, bytes out. Throttle counters work either way.
"""

from __future__ import annotations

from typing import cast

from redis.asyncio import Redis

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    """Build the async Redis client used by FSM + throttle.

    No I/O happens here — redis-py opens connections lazily on first
    command. Call ``await client.ping()`` if you need to verify the
    server is reachable at startup.
    """
    # ``Redis.from_url`` is typed as returning ``Any`` in the bundled
    # redis-py stubs (the classmethod can't easily express ``cls``).
    # Cast restores the precise return type for our callers.
    return cast(Redis, Redis.from_url(str(settings.redis_url), decode_responses=False))
