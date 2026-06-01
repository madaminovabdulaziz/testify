"""Block until MySQL accepts a real ``bot``-user connection on ``DB_HOST:DB_PORT``.

The fresh-volume init of the official mysql image runs through ~3
restart cycles before the configured user is created and the server is
listening on TCP. ``mysqladmin ping`` returns "alive" too early — it
reports the bootstrap mysqld, not the user-facing one. This script
polls the actual credentials the app will use, which is the strict
readiness signal we want before running ``alembic upgrade head``.

Reads ``DB_HOST`` / ``DB_PORT`` / ``DB_USER`` / ``DB_PASSWORD`` /
``DB_NAME`` from the environment. Exits 0 on first successful
``SELECT 1``; exits 1 after ``WAIT_MYSQL_TIMEOUT`` seconds (default 90)
with a clear error.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import asyncmy

_TIMEOUT_SECONDS = int(os.environ.get("WAIT_MYSQL_TIMEOUT", "90"))
_POLL_SECONDS = 1.0


async def _try_connect() -> str | None:
    """Return ``None`` on success, error string on failure."""
    try:
        conn = await asyncmy.connect(
            host=os.environ["DB_HOST"],
            port=int(os.environ["DB_PORT"]),
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            db=os.environ["DB_NAME"],
        )
    except Exception as exc:
        return type(exc).__name__ + ": " + str(exc)

    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1")
            await cursor.fetchone()
    except Exception as exc:
        return type(exc).__name__ + ": " + str(exc)
    finally:
        conn.close()
    return None


async def _main() -> int:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    attempt = 0
    last_error = "(no attempts made)"
    while time.monotonic() < deadline:
        attempt += 1
        last_error = await _try_connect() or ""
        if last_error == "":
            print(
                f"▶ MySQL ready (bot user connected on attempt {attempt}).",
                flush=True,
            )
            return 0
        # Quiet by default — only print every 5 attempts so the log stays small.
        if attempt == 1 or attempt % 5 == 0:
            print(
                f"  [{attempt}] not ready yet: {last_error[:120]}",
                flush=True,
            )
        await asyncio.sleep(_POLL_SECONDS)

    print(
        f"✗ MySQL did not become ready within {_TIMEOUT_SECONDS}s.\n  Last error: {last_error}",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    try:
        return asyncio.run(_main())
    except KeyError as exc:
        print(f"✗ Missing required env var: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
