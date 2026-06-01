"""Custom exception hierarchy.

Verbatim from ARCHITECTURE_SPEC §16.1, with constructor support for the
exceptions that carry extra payload (parse errors, existing attempts).

The global error handler (later prompts) distinguishes :class:`UserError`
— surfaced to the student as their ``user_message`` and not logged at
``error`` level — from everything else, which is logged + sent to Sentry +
results in a generic "try again later" reply.
"""

from __future__ import annotations


class BotError(Exception):
    """Root of every domain-level error raised by the bot."""


class UserError(BotError):
    """An error worth showing to the user verbatim.

    Subclasses set ``user_message`` to a finalized Russian string. Default
    is a safe generic so a bare ``raise UserError()`` still says something
    sensible.
    """

    user_message: str = "Произошла ошибка. Попробуйте позже."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.user_message)


class InvalidNameError(UserError):
    user_message = "Пожалуйста, введите корректное имя."


class ReceiptLimitExceededError(UserError):
    user_message = "У вас уже есть чеки на проверке."


class ReceiptAlreadyPendingError(UserError):
    """Same image is already in the user's pending queue (silent re-submit)."""

    user_message = "Этот чек уже отправлен на проверку."


class ReceiptAlreadyProcessedError(UserError):
    """Second admin tapped Approve/Reject after the first one already resolved the receipt."""

    user_message = "Этот чек уже обработан."


class ReceiptUserBannedError(UserError):
    """Tried to approve a receipt belonging to a banned user — the ban must win.

    Raised by ``ReceiptService.approve`` so the whole request transaction
    rolls back: the receipt stays pending and the user stays banned. The
    admin is told to lift the ban first (CODE_REVIEW C2).
    """

    user_message = "Пользователь заблокирован — чек не одобрен."


class TestParseError(UserError):
    """Excel test upload failed validation; ``errors`` lists (line, message)."""

    user_message = "Не удалось разобрать файл. Проверьте формат."

    def __init__(self, errors: list[tuple[int, str]]) -> None:
        self.errors: list[tuple[int, str]] = list(errors)
        super().__init__(self.user_message)


class NotApprovedError(UserError):
    user_message = "Сначала нужно оплатить подготовку."


class NoActiveTestError(UserError):
    user_message = "Сейчас нет доступных тестов."


class PublishConflictError(UserError):
    """Two admins published at the same moment; the DB's one-active-test
    unique index rejected the second activation (CODE_REVIEW C8).

    A ``UserError`` so it propagates to the middleware (clean rollback) and
    is shown friendly rather than logged to Sentry as a crash.
    """

    user_message = "Тест публикуется другим администратором. Попробуйте ещё раз через секунду."


class AttemptAlreadyExistsError(UserError):
    """User tried to start a test they've already taken.

    ``existing_attempt_id`` is the prior attempt when known (so the handler
    can resume / show its result). It is ``None`` when we only learn of the
    clash from a unique-constraint ``IntegrityError`` on a concurrent start
    tap — the session is then mid-rollback, so we can't re-read the id and
    just surface the friendly message (CODE_REVIEW M1).
    """

    user_message = "Вы уже проходили этот тест."

    def __init__(self, existing_attempt_id: int | None = None) -> None:
        self.existing_attempt_id = existing_attempt_id
        super().__init__(self.user_message)


class AttemptNotVisibleError(UserError):
    """A callback referenced an attempt that doesn't belong to this user.

    Normal user behaviour (tapping a stale button from an earlier session),
    so it's a ``UserError`` shown friendly rather than a ``SystemError`` that
    spams Sentry (CODE_REVIEW M5).
    """

    user_message = "Эта попытка теста недоступна."


# Intentionally named ``SystemError`` per ARCHITECTURE_SPEC §16.1. This
# shadows Python's built-in inside ``app.exceptions``; callers should
# import it qualified (``from app.exceptions import SystemError``) so the
# ambiguity is explicit.
class SystemError(BotError):
    """Unexpected error; logged + Sentry + generic message to user."""
