"""6-character payment reference codes.

Each new user gets one of these stamped on their payment instructions so
the admin can match an incoming bank deposit against the right student
(PRODUCT_BLUEPRINT §8.1 step 5). The alphabet deliberately excludes
glyphs that look the same in handwriting on a bank receipt — ``0/O``,
``1/I/L`` — so the admin doesn't have to guess between O and 0 when
matching codes by eye.
"""

from __future__ import annotations

import secrets
from typing import Final

from app.repositories.user_repository import UserRepository

# A-Z minus {I, L, O}; 0-9 minus {0, 1}. 23 letters + 8 digits = 31 glyphs.
ALPHABET: Final[str] = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH: Final[int] = 6
MAX_ATTEMPTS: Final[int] = 5


class ReferenceCodeGenerationError(RuntimeError):
    """Raised when ``MAX_ATTEMPTS`` consecutive draws all collide with existing codes."""


class ReferenceCodeService:
    """Allocate a globally-unique reference code via repository round-trip."""

    def __init__(self, user_repository: UserRepository) -> None:
        self._user_repository = user_repository

    async def generate_unique(self) -> str:
        """Return a fresh, never-before-used 6-character code.

        Draws are cryptographically random (``secrets.choice``) — at 31^6 ≈
        887M values per code the collision rate is far below the 5-retry
        ceiling even at v1.4's projected scale, so a hit is a near-certain
        signal of bug or database corruption. We raise rather than loop
        forever.
        """
        for _ in range(MAX_ATTEMPTS):
            candidate = _draw()
            existing = await self._user_repository.get_by_reference_code(candidate)
            if existing is None:
                return candidate

        raise ReferenceCodeGenerationError(
            f"Could not allocate a unique reference code after {MAX_ATTEMPTS} attempts."
        )


def _draw() -> str:
    """One independent random draw from the confusable-free alphabet."""
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
