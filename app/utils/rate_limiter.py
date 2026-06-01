"""Asyncio token-bucket rate limiter.

A 30-message-per-second Telegram cap (ARCHITECTURE_SPEC §12) is enforced
on the broadcast path; this is the bucket the broadcast pulls a token
from before each send. Kept dependency-free per the spec note ("~30
lines, no new deps").
"""

from __future__ import annotations

import asyncio
import time


class AsyncTokenBucket:
    """Token-bucket limiter for coroutines.

    Tokens refill at ``rate`` per second up to ``capacity``. ``acquire``
    blocks until at least ``tokens`` are available, then drains them.
    Safe to share across concurrent tasks — internal access is guarded
    by an asyncio lock so the bucket math doesn't race.
    """

    __slots__ = ("_capacity", "_last_refill", "_lock", "_rate", "_tokens")

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = rate
        self._capacity = float(capacity) if capacity is not None else float(rate)
        if self._capacity <= 0:
            raise ValueError("capacity must be positive")
        # Start full so the first ``capacity`` calls go through without waiting.
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until ``tokens`` tokens are available, then consume them."""
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        if tokens > self._capacity:
            raise ValueError("cannot acquire more tokens than capacity")
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self._last_refill)
                self._last_refill = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait_for = deficit / self._rate
            await asyncio.sleep(wait_for)
