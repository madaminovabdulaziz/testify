"""Timezone-aware datetime helpers.

Everything in the DB is stored in UTC (ARCHITECTURE_SPEC §4.5). The only
place we convert to local time is in views that render dates for the
student (Asia/Tashkent, UTC+5). Code never calls ``datetime.now()``
directly — it calls :func:`now_utc` so timezone-naive datetimes cannot
accidentally enter the system.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

# Asia/Tashkent is a fixed-offset zone (no DST). A simple ``timezone`` is
# enough and avoids a tzdata dependency on minimal containers.
TASHKENT_TZ: timezone = timezone(timedelta(hours=5), name="Asia/Tashkent")


def now_utc() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def to_tashkent(dt: datetime) -> datetime:
    """Convert a timezone-aware datetime to Asia/Tashkent for display.

    Raises ``ValueError`` if ``dt`` is naive — we never silently assume UTC,
    that's how bugs sneak in.
    """
    if dt.tzinfo is None:
        raise ValueError("Refusing to convert a naive datetime; expected tz-aware UTC.")
    return dt.astimezone(TASHKENT_TZ)


def format_duration_mm_ss(seconds: int) -> str:
    """Format a non-negative number of seconds as ``MM:SS`` (e.g. ``42:15``)."""
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def format_timestamp_local(dt: datetime) -> str:
    """Format a UTC datetime in Asia/Tashkent as ``YYYY-MM-DD HH:MM``.

    ``dt`` MUST be timezone-aware. DB-sourced values come in aware via
    the :class:`app.models.types.UTCDateTime` column wrapper; app-side
    constructions go through :func:`now_utc`. A naive ``dt`` here is a
    bug — :func:`to_tashkent` raises ``ValueError`` so it surfaces.
    """
    return to_tashkent(dt).strftime("%Y-%m-%d %H:%M")
