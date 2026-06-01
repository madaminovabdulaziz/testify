"""Custom SQLAlchemy column types used across the project.

Lives outside individual model files so the same type can be reused
without circular imports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """``DATETIME(fsp)`` that always round-trips as ``tzinfo=UTC``.

    MySQL itself stores no timezone info. We configure the server with
    ``--default-time-zone=+00:00`` (see ``docker-compose*.yml``) so the
    stored values represent UTC, but ``asyncmy`` hands them back as
    *naive* Python ``datetime`` objects. That naive value then trips
    :func:`app.utils.datetime.to_tashkent`'s strict "I don't accept
    naive datetimes" guard, and any arithmetic against a tz-aware
    ``now_utc()`` raises ``TypeError``.

    This wrapper:

    * **on write** — accepts aware *or* naive datetimes, converts aware
      ones to UTC, strips ``tzinfo`` (MySQL won't accept it on plain
      ``DATETIME``);
    * **on read** — attaches ``tzinfo=UTC`` to whatever the driver
      returns, so application code always sees aware UTC datetimes.

    Apply it once per column (``mapped_column(UTCDateTime(fsp=6), ...)``)
    instead of the bare dialect type and the entire "naive datetime
    from MySQL" bug class disappears.
    """

    impl = DATETIME
    cache_ok = True

    def __init__(self, fsp: int = 6) -> None:
        super().__init__(fsp=fsp)

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
