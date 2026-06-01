"""Unit tests for :class:`app.services.reference_code.ReferenceCodeService`."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.reference_code import (
    ALPHABET,
    CODE_LENGTH,
    MAX_ATTEMPTS,
    ReferenceCodeGenerationError,
    ReferenceCodeService,
)

# Glyphs the alphabet must NEVER emit per PRODUCT_BLUEPRINT §8.1 / §14.1.
_CONFUSABLES = "0O1IL"


def _repo_returning(*sequence: object) -> AsyncMock:
    """Build an async ``UserRepository`` mock whose lookups return the given sequence."""
    repo = AsyncMock()
    repo.get_by_reference_code = AsyncMock(side_effect=list(sequence))
    return repo


# ---------- alphabet hygiene ----------


def test_alphabet_excludes_confusables() -> None:
    for bad in _CONFUSABLES:
        assert bad not in ALPHABET


def test_alphabet_size_matches_expectation() -> None:
    # 23 letters (A-Z minus I, L, O) + 8 digits (2-9) = 31 glyphs.
    assert len(ALPHABET) == 31


# ---------- happy path ----------


async def test_generate_unique_first_draw_wins() -> None:
    repo = _repo_returning(None)
    svc = ReferenceCodeService(repo)

    code = await svc.generate_unique()

    assert len(code) == CODE_LENGTH
    assert all(ch in ALPHABET for ch in code)
    assert not any(bad in code for bad in _CONFUSABLES)
    assert repo.get_by_reference_code.await_count == 1


async def test_generate_unique_retries_on_collision_then_succeeds() -> None:
    # First two draws collide with existing users; the third is free.
    repo = _repo_returning(MagicMock(), MagicMock(), None)
    svc = ReferenceCodeService(repo)

    code = await svc.generate_unique()

    assert len(code) == CODE_LENGTH
    assert repo.get_by_reference_code.await_count == 3


async def test_generate_unique_raises_after_max_attempts() -> None:
    # Every draw collides → service gives up after MAX_ATTEMPTS retries.
    repo = _repo_returning(*[MagicMock()] * MAX_ATTEMPTS)
    svc = ReferenceCodeService(repo)

    with pytest.raises(ReferenceCodeGenerationError):
        await svc.generate_unique()

    assert repo.get_by_reference_code.await_count == MAX_ATTEMPTS


# ---------- statistical hygiene ----------


async def test_many_draws_never_contain_confusables() -> None:
    """A property test (light): 200 codes, never a forbidden glyph."""
    repo = AsyncMock()
    repo.get_by_reference_code = AsyncMock(return_value=None)
    svc = ReferenceCodeService(repo)

    for _ in range(200):
        code = await svc.generate_unique()
        assert len(code) == CODE_LENGTH
        assert all(ch in ALPHABET for ch in code)
        assert not any(bad in code for bad in _CONFUSABLES)


async def test_many_draws_use_the_full_alphabet() -> None:
    """Across a large sample, every glyph in the alphabet shows up at least once."""
    repo = AsyncMock()
    repo.get_by_reference_code = AsyncMock(return_value=None)
    svc = ReferenceCodeService(repo)

    seen: set[str] = set()
    for _ in range(2000):
        code = await svc.generate_unique()
        seen.update(code)
    # 2000 codes × 6 chars = 12000 draws from 31-char alphabet; coverage is virtually certain.
    assert set(ALPHABET).issubset(seen)
