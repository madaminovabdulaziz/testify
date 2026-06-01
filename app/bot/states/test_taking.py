"""FSM states for an in-progress test session (PRODUCT_BLUEPRINT §8.5)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TestState(StatesGroup):
    """A user has an attempt in flight.

    ``in_progress`` is the default view; ``confirming_finish`` is the
    intermediate state after tapping «🏁 Завершить тест» — the user is
    on the Да / Продолжить confirmation dialog.
    """

    in_progress = State()
    confirming_finish = State()
