"""Unit tests for the jobs runtime container holder."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.jobs._runtime import (
    get_runtime_container,
    reset_runtime_container,
    set_runtime_container,
)


def teardown_function() -> None:
    """Always clear the holder so module-level state doesn't leak across tests."""
    reset_runtime_container()


def test_get_before_set_raises() -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        get_runtime_container()


def test_set_then_get_returns_same_instance() -> None:
    container = MagicMock()
    set_runtime_container(container)
    assert get_runtime_container() is container


def test_reset_clears_the_holder() -> None:
    set_runtime_container(MagicMock())
    reset_runtime_container()
    with pytest.raises(RuntimeError):
        get_runtime_container()
