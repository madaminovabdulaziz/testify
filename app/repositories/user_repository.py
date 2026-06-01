"""Data access for the ``users`` table.

Method set is driven by ``UserService`` (ARCHITECTURE_SPEC §8.1) and the
query patterns in DATABASE_SPEC §10.1, §10.4, §10.12.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select, update

from app.models.user import User
from app.repositories.base import BaseRepository
from app.utils.datetime import now_utc
from app.utils.text import normalize_phone


class UserRepository(BaseRepository):
    """Reads + writes for ``users``. Pure SQL — no business rules."""

    async def get_by_id(self, user_id: int) -> User | None:
        """Fetch one user by surrogate id, refreshed from the DB.

        ``populate_existing=True`` so a re-read after a Core ``update()``
        (``mark_approved``, ``set_status`` …) reflects the new column values
        rather than a stale identity-map copy (see AttemptRepository.get_by_id).
        """
        return await self._session.get(User, user_id, populate_existing=True)

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        """Fetch one user by Telegram id — used on every incoming update."""
        stmt = select(User).where(User.telegram_id == telegram_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(self, telegram_id: int, username: str | None) -> User:
        """Insert a brand-new ``status='new'`` user and return the row."""
        user = User(telegram_id=telegram_id, username=username, status="new")
        self._session.add(user)
        await self._session.flush()
        return user

    async def set_phone(self, user_id: int, phone: str) -> None:
        """Persist the phone number captured during onboarding."""
        stmt = update(User).where(User.id == user_id).values(phone=phone)
        await self._session.execute(stmt)

    async def set_name(self, user_id: int, full_name: str) -> None:
        """Persist the full name captured during onboarding."""
        stmt = update(User).where(User.id == user_id).values(full_name=full_name)
        await self._session.execute(stmt)

    async def set_reference_code(self, user_id: int, reference_code: str) -> None:
        """Attach a freshly generated 6-char payment reference code."""
        stmt = update(User).where(User.id == user_id).values(reference_code=reference_code)
        await self._session.execute(stmt)

    async def set_status(self, user_id: int, status: str) -> None:
        """Transition the user to ``status``. Caller validates the target value."""
        stmt = update(User).where(User.id == user_id).values(status=status)
        await self._session.execute(stmt)

    async def mark_approved(self, user_id: int) -> int:
        """Promote to ``approved`` and stamp ``approved_at`` if unset. Returns rowcount.

        Idempotent: re-approving an already-approved user leaves the original
        ``approved_at`` in place (per DATABASE_SPEC §5.1 — the timestamp is
        preserved across later status changes like ``banned``).

        Guarded with ``status <> 'banned'`` so approving a stale pending
        receipt can never silently un-ban a user (CODE_REVIEW C2). The
        caller disambiguates a 0 rowcount (banned vs. already-approved,
        which under MySQL's rows-changed semantics also reports 0).
        """
        stmt = (
            update(User)
            .where(User.id == user_id, User.status != "banned")
            .values(
                status="approved",
                approved_at=func.coalesce(User.approved_at, now_utc()),
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def mark_rejected(self, user_id: int) -> int:
        """Return the user to ``rejected`` so they can resubmit. Returns rowcount.

        Guarded with ``status <> 'banned'`` for the same reason as
        :meth:`mark_approved` — rejecting a banned user's leftover pending
        receipt must not flip them back to ``rejected`` and undo the ban.
        """
        stmt = (
            update(User)
            .where(User.id == user_id, User.status != "banned")
            .values(status="rejected")
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def mark_bot_blocked(self, user_id: int) -> None:
        """Set the ``bot_blocked`` flag after a ``TelegramForbiddenError``."""
        stmt = update(User).where(User.id == user_id).values(bot_blocked=True)
        await self._session.execute(stmt)

    async def clear_bot_blocked(self, user_id: int) -> None:
        """Clear ``bot_blocked`` once the user reaches the bot again (CODE_REVIEW L2)."""
        stmt = (
            update(User)
            .where(User.id == user_id, User.bot_blocked.is_(True))
            .values(bot_blocked=False)
        )
        await self._session.execute(stmt)

    async def get_by_reference_code(self, reference_code: str) -> User | None:
        """Precise lookup by ``reference_code`` — used by ``ReferenceCodeService``."""
        stmt = select(User).where(User.reference_code == reference_code)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_approved_by_phone(self, phone: str, *, exclude_user_id: int) -> User | None:
        """An ``approved`` user (other than ``exclude_user_id``) with this phone, if any.

        Feeds the soft phone-uniqueness warning on receipt submission
        (PRODUCT_BLUEPRINT §14.2 / CODE_REVIEW M8). Phones are stored
        normalized (H18) so the equality match is reliable.
        """
        stmt = (
            select(User)
            .where(
                User.phone == phone,
                User.status == "approved",
                User.id != exclude_user_id,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_by_query(self, query: str) -> User | None:
        """``/find`` admin command — match an exact phone, username, or ref code.

        The phone branch matches on the normalized digits-only form so
        ``+998…`` and ``998…`` both resolve (CODE_REVIEW H18); username and
        reference code stay exact (minus a leading ``@``/``#``).
        """
        normalized = query.lstrip("@").lstrip("#").strip()
        stmt = (
            select(User)
            .where(
                or_(
                    User.phone == normalize_phone(query),
                    User.username == normalized,
                    User.reference_code == normalized.upper(),
                )
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_approved_for_broadcast(self) -> list[tuple[int, int]]:
        """Return ``(id, telegram_id)`` of every approved, non-blocked user."""
        stmt = (
            select(User.id, User.telegram_id)
            .where(User.status == "approved", User.bot_blocked.is_(False))
            .order_by(User.id)
        )
        result = await self._session.execute(stmt)
        return [(row.id, row.telegram_id) for row in result]

    async def count_by_status(self) -> dict[str, int]:
        """``{status: row_count}`` across the table — feeds the /stats command."""
        stmt = select(User.status, func.count()).group_by(User.status)
        rows = await self._session.execute(stmt)
        return {status: int(count) for status, count in rows}

    async def count_total(self) -> int:
        """Total user count regardless of status."""
        stmt = select(func.count()).select_from(User)
        return int((await self._session.execute(stmt)).scalar_one())
