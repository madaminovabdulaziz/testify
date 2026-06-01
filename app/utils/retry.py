"""Async retry decorator with exponential backoff + jitter.

Used (in later prompts) to wrap Telegram API calls that may transiently
fail with network or 5xx errors. Not used for ``TelegramRetryAfter`` —
that one already carries a precise ``retry_after`` value and is handled
inline in :mod:`app.services.notification_service`.
"""

from __future__ import annotations

import asyncio
import functools
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


def async_retry(
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    factor: float = 2.0,
    jitter: float = 0.2,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator factory: retry an async function up to ``attempts`` times.

    Backoff schedule for the default values: ~0.5s, ~1.0s, ~2.0s, capped
    at ``max_delay``. ``jitter`` is a fraction in [0, 1) that perturbs each
    sleep by ``±jitter * sleep`` to avoid thundering-herd retries.

    The decorated function's return type is preserved through ``TypeVar``.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = base_delay
            for attempt in range(1, attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions:
                    if attempt == attempts:
                        raise
                    sleep_for = min(delay, max_delay) * (1 + random.uniform(-jitter, jitter))
                    await asyncio.sleep(max(0.0, sleep_for))
                    delay *= factor
            # Unreachable: either we returned or we re-raised on the final attempt.
            raise AssertionError("async_retry exited its loop unexpectedly")

        return wrapper

    return decorator
