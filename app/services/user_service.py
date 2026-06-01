"""User lifecycle: identity, onboarding, approval, ban list.

ARCHITECTURE_SPEC §8.1 + PRODUCT_BLUEPRINT §10.1 (state machine). Each
operation that triggers a status transition bundles the persistence
write and the status flip so callers can't accidentally split them.
State-machine guards live here as a defense-in-depth check: handlers
should already be FSM-gated, but if something gets through to the wrong
service method we no-op + log rather than corrupt the row.
"""

from __future__ import annotations

import unicodedata

import structlog

from app.exceptions import InvalidNameError
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.utils.text import normalize_phone

logger = structlog.get_logger()

# Per PRODUCT_BLUEPRINT §8.1 step 4.
_NAME_MIN_LEN = 2
_NAME_MAX_LEN = 80
# Unicode general-category prefixes we reject in names (CODE_REVIEW M2):
#   C* — control / format (incl. the RTL-override that flips admin captions)
#   S* — symbols, including emoji (a name "как в документе" has none)
_REJECTED_NAME_CATEGORY_PREFIXES = ("C", "S")
# Plus line/paragraph separators (regular spaces, category Zs, stay allowed).
_REJECTED_NAME_CATEGORIES = frozenset({"Zl", "Zp"})


class UserService:
    """Drives the user funnel and the ``users`` table mutations."""

    def __init__(self, user_repository: UserRepository) -> None:
        self._users = user_repository

    # ---------- identity ----------

    async def get_or_create(self, telegram_id: int, username: str | None) -> User:
        """Return the user row for ``telegram_id``; create one in ``status='new'`` if absent."""
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is not None:
            return user
        return await self._users.create(telegram_id=telegram_id, username=username)

    async def get_user(self, user_id: int) -> User | None:
        """Fetch one user by surrogate id (for jobs/services that already know it)."""
        return await self._users.get_by_id(user_id)

    async def count_by_status(self) -> dict[str, int]:
        """``{status: count}`` across the users table — feeds the /stats command."""
        return await self._users.count_by_status()

    async def count_total(self) -> int:
        """Total user count regardless of status."""
        return await self._users.count_total()

    async def find(self, query: str) -> User | None:
        """``/find`` admin command — exact match by phone, username, or reference code."""
        return await self._users.find_by_query(query)

    async def list_approved_for_broadcast(self) -> list[tuple[int, int]]:
        """Return ``(user_id, telegram_id)`` for every approved, non-blocked user."""
        return await self._users.list_approved_for_broadcast()

    # ---------- onboarding ----------

    async def start_onboarding(self, user_id: int) -> None:
        """Mark a brand-new user as being mid-onboarding (``status='onboarding_phone'``).

        Called by the welcome-screen "Начать ▶️" callback so a later
        ``/start`` from the same user routes back to the contact-request
        step instead of replaying the welcome.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            logger.warning("start_onboarding_missing_user", user_id=user_id)
            return
        if user.status != "new":
            # Already past this point — leave the row alone.
            logger.info(
                "start_onboarding_unexpected_status",
                user_id=user_id,
                status=user.status,
            )
            return
        await self._users.set_status(user_id, "onboarding_phone")

    async def set_phone(self, user_id: int, phone: str) -> None:
        """Persist the phone captured from a shared contact and advance to ``onboarding_name``.

        Status is checked **before** the write (CODE_REVIEW H6/H13): a user
        who is past onboarding (e.g. already ``approved``) must never have
        their phone silently overwritten by a stale contact-share.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            logger.warning("set_phone_missing_user", user_id=user_id)
            return
        if user.status not in ("new", "onboarding_phone"):
            logger.info(
                "set_phone_unexpected_status",
                user_id=user_id,
                status=user.status,
            )
            return
        # Store the canonical digits-only form so /find matches regardless of
        # how the admin types the number later (CODE_REVIEW H18).
        normalized = normalize_phone(phone)
        if not normalized:
            # An empty/garbage contact share — don't store a blank phone or
            # advance the funnel on it (CODE_REVIEW L4).
            logger.info("set_phone_empty", user_id=user_id)
            return
        await self._users.set_phone(user_id, normalized)
        await self._users.set_status(user_id, "onboarding_name")

    async def set_name(self, user_id: int, name: str) -> None:
        """Validate + persist the user's full name. Raises ``InvalidNameError`` on bad input.

        Does NOT advance status — the handler also generates a reference
        code, and we want both to land atomically. See
        :meth:`attach_reference_code` for the actual status flip.

        Validation runs first so a bad name is rejected regardless of
        status; the write only happens while the user is in the
        name-capture step (CODE_REVIEW H6/H13 — don't overwrite a settled
        user's name).
        """
        # NFC-normalize so visually-identical names compare equal and stray
        # combining sequences collapse (CODE_REVIEW M2).
        cleaned = unicodedata.normalize("NFC", name).strip()
        if not _is_valid_name(cleaned):
            raise InvalidNameError()
        user = await self._users.get_by_id(user_id)
        if user is None:
            logger.warning("set_name_missing_user", user_id=user_id)
            return
        if user.status != "onboarding_name":
            logger.info(
                "set_name_unexpected_status",
                user_id=user_id,
                status=user.status,
            )
            return
        await self._users.set_name(user_id, cleaned)

    async def attach_reference_code(self, user_id: int, reference_code: str) -> None:
        """Attach a generated reference code and advance the user to ``pending_payment``.

        Status check is **above** the write (CODE_REVIEW H6): an approved
        user must keep their original ``reference_code`` — it's the admin's
        link between the code and the bank deposit. Rewriting it would break
        that audit trail.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            logger.warning("attach_reference_code_missing_user", user_id=user_id)
            return
        if user.status not in ("onboarding_name", "new", "onboarding_phone"):
            logger.info(
                "attach_reference_code_unexpected_status",
                user_id=user_id,
                status=user.status,
            )
            return
        await self._users.set_reference_code(user_id, reference_code)
        await self._users.set_status(user_id, "pending_payment")

    # ---------- approval funnel ----------

    async def mark_pending_approval(self, user_id: int) -> None:
        """Receipt submitted — move the user from ``pending_payment``/``rejected`` to ``pending_approval``."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            logger.warning("mark_pending_approval_missing_user", user_id=user_id)
            return
        if user.status not in ("pending_payment", "rejected"):
            logger.info(
                "mark_pending_approval_unexpected_status",
                user_id=user_id,
                status=user.status,
            )
            return
        await self._users.set_status(user_id, "pending_approval")

    async def mark_approved(self, user_id: int) -> int:
        """Admin approved the receipt — promote the user to ``approved`` (idempotent).

        Returns the UPDATE rowcount. A 0 means the write was blocked by the
        ``status <> 'banned'`` guard **or** the user was already approved and
        nothing changed; the caller (``ReceiptService.approve``) re-reads to
        tell those apart.
        """
        return await self._users.mark_approved(user_id)

    async def mark_rejected(self, user_id: int) -> None:
        """Admin rejected the receipt — return the user to ``rejected`` so they can resubmit.

        No-ops (with a log) if the user is banned: a rejection must not
        un-ban them. The receipt itself is still marked rejected by the
        caller; the user simply stays banned.
        """
        rowcount = await self._users.mark_rejected(user_id)
        if rowcount == 0:
            logger.info("mark_rejected_noop_banned_or_missing", user_id=user_id)

    # ---------- moderation ----------

    async def ban(self, user_id: int) -> None:
        """Mark the user as ``banned`` from any source state."""
        await self._users.set_status(user_id, "banned")

    async def unban(self, user_id: int) -> bool:
        """Restore a banned user to ``approved``. Returns whether they were restored.

        Only users who were *approved before* the ban are restored — we key
        off ``approved_at``, which survives a ban (DATABASE_SPEC §5.1). A user
        banned from a pre-approval state has no ``approved_at`` and must NOT be
        silently granted access via /unban (CODE_REVIEW M16); the admin walks
        them back through onboarding manually instead.
        """
        user = await self._users.get_by_id(user_id)
        if user is None or user.status != "banned":
            logger.info(
                "unban_unexpected_status",
                user_id=user_id,
                status=user.status if user else None,
            )
            return False
        if user.approved_at is None:
            logger.info("unban_refused_never_approved", user_id=user_id)
            return False
        await self._users.set_status(user_id, "approved")
        return True

    async def mark_bot_blocked(self, user_id: int) -> None:
        """Flag a user as having blocked the bot so broadcasts skip them."""
        await self._users.mark_bot_blocked(user_id)

    async def clear_bot_blocked(self, user_id: int) -> None:
        """Clear the ``bot_blocked`` flag — the user can reach us again (CODE_REVIEW L2)."""
        await self._users.clear_bot_blocked(user_id)


def _is_valid_name(name: str) -> bool:
    """PRODUCT_BLUEPRINT §8.1 step 4 + CODE_REVIEW M2.

    2–80 chars, at least one letter, and no control/format chars (RTL
    overrides, zero-width joiners), emoji/symbols, or exotic separators —
    which would otherwise let "79 emoji + 1 letter" or an RTL-injection name
    through and corrupt the admin-group caption.
    """
    if not _NAME_MIN_LEN <= len(name) <= _NAME_MAX_LEN:
        return False
    if not any(ch.isalpha() for ch in name):
        return False
    for ch in name:
        category = unicodedata.category(ch)
        if category[0] in _REJECTED_NAME_CATEGORY_PREFIXES:
            return False
        if category in _REJECTED_NAME_CATEGORIES:
            return False
    return True
