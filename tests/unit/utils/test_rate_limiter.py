"""Unit tests for :class:`app.utils.rate_limiter.AsyncTokenBucket`."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.utils.rate_limiter import AsyncTokenBucket


def test_invalid_rate_raises() -> None:
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate=0)
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate=-1)


def test_invalid_capacity_raises() -> None:
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate=10, capacity=0)
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate=10, capacity=-5)


def test_invalid_acquire_amount_raises() -> None:
    bucket = AsyncTokenBucket(rate=10, capacity=5)
    with pytest.raises(ValueError):
        asyncio.get_event_loop().run_until_complete(bucket.acquire(tokens=0))
    with pytest.raises(ValueError):
        asyncio.get_event_loop().run_until_complete(bucket.acquire(tokens=10))


async def test_bucket_starts_full() -> None:
    bucket = AsyncTokenBucket(rate=100, capacity=5)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    # Five immediate consumes should finish well under the refill time
    # for one extra token (10ms).
    assert elapsed < 0.05


async def test_bucket_throttles_after_burst() -> None:
    """Once tokens drain, the next acquire waits roughly 1/rate seconds."""
    bucket = AsyncTokenBucket(rate=20, capacity=2)
    # Burn the initial two tokens immediately.
    await bucket.acquire()
    await bucket.acquire()
    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start
    # Next token refills at 20 Hz → ~0.05s. Allow generous lower bound
    # since the lock + scheduling overhead can shave a few ms.
    assert 0.025 < elapsed < 0.2


async def test_bucket_serializes_concurrent_callers() -> None:
    """Three concurrent acquires from an empty bucket all wait properly."""
    bucket = AsyncTokenBucket(rate=50, capacity=1)
    # Drain.
    await bucket.acquire()
    start = time.monotonic()
    # Three concurrent waiters should each take ~20ms (1/50s) cumulatively.
    await asyncio.gather(bucket.acquire(), bucket.acquire(), bucket.acquire())
    elapsed = time.monotonic() - start
    # Lower bound: at least 2 refills (~40ms). Upper bound: generous slop.
    assert elapsed > 0.04
    assert elapsed < 0.5
