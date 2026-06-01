"""Callback data factories for the test-taking screen (ARCHITECTURE_SPEC §6.5).

All three packed payloads stay well under Telegram's 64-byte limit:
short prefixes + small ints + single-letter option.
"""

from __future__ import annotations

from typing import Literal

from aiogram.filters.callback_data import CallbackData


class TestAnswerCD(CallbackData, prefix="ta"):
    """User tapped one of the A/B/C/D buttons for the current question."""

    # Tell pytest this isn't a unittest-style test class — the ``Test*``
    # python_classes pattern would otherwise match when test modules
    # import this name into their namespace.
    __test__ = False

    attempt_id: int
    question_pos: int  # 1..50
    option: Literal["A", "B", "C", "D"]


class TestNavCD(CallbackData, prefix="tn"):
    """User tapped a nav button (← / →) or a number in the grid.

    ``target_pos == 0`` is reserved for "jump to next unanswered" if a
    future iteration adds that shortcut button.
    """

    __test__ = False

    attempt_id: int
    target_pos: int  # 0..50


class TestFinishCD(CallbackData, prefix="tf"):
    """User tapped «🏁 Завершить тест» (confirmed=False) or the Да button (confirmed=True)."""

    __test__ = False

    attempt_id: int
    confirmed: bool
