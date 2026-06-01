"""Test-attempt lifecycle.

ARCHITECTURE_SPEC §8.4 + §11. The service owns:

* the "approved + no prior attempt" pre-checks at ``start``
* the warning + expiry job registration on APScheduler at start time
* the per-button-tap state assembly the test screen view consumes
* the *idempotent* ``finish`` (calling it twice from a user-tap and an
  expiry job races, and we must not double-score the row)

The scheduler-invoked callables themselves live in
:mod:`app.jobs.attempt_timer`; this service only registers / cancels
them by deterministic id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.exc import IntegrityError

from app.exceptions import (
    AttemptAlreadyExistsError,
    AttemptNotVisibleError,
    NotApprovedError,
    SystemError,
)
from app.jobs.registry import cancel_attempt_jobs, schedule_attempt_jobs
from app.models.answer import Answer
from app.models.attempt import Attempt
from app.models.question import Question
from app.models.test import Test
from app.models.user import User
from app.repositories.answer_repository import AnswerRepository
from app.repositories.attempt_repository import (
    AttemptRepository,
    AttemptScores,
    LeaderboardEntry,
    WarningSlot,
)
from app.repositories.question_repository import QuestionRepository
from app.services.scoring_service import (
    ScoringService,
    SectionScores,
    section_scores_from_attempt,
)
from app.utils.datetime import now_utc

logger = structlog.get_logger()

FinishReason = Literal["user", "expired"]


@dataclass(frozen=True)
class AttemptState:
    """Everything the test-screen view needs to render one tick of the test."""

    attempt_id: int
    user_id: int
    test_id: int
    status: str
    current_position: int
    started_at: datetime
    expires_at: datetime
    time_remaining_seconds: int
    questions: tuple[Question, ...]
    answers_by_question_id: dict[int, Answer]


@dataclass(frozen=True)
class AttemptResult:
    """What :meth:`AttemptService.finish` returns to the caller.

    ``owned_finalization`` is True only for the single call that actually
    flipped the attempt ``in_progress → submitted/expired``. It lets the
    expiry job decide whether to DM the user (it must stay quiet if a manual
    finish already did — CODE_REVIEW H2). Callers that just want the scores
    can ignore it.
    """

    attempt: Attempt
    scores: SectionScores
    owned_finalization: bool = False


@dataclass(frozen=True)
class AttemptDetail:
    """Snapshot of one attempt with its questions + answers.

    The handler enriches this with ``user`` / ``test`` rows (loaded
    via the matching services) before rendering — ``AttemptService``
    deliberately doesn't take ``UserRepository`` / ``TestRepository``
    as constructor args.
    """

    attempt: Attempt
    questions: tuple[Question, ...]
    answers_by_question_id: dict[int, Answer]


class AttemptService:
    """Reads + writes for the in-flight test session."""

    def __init__(
        self,
        attempt_repository: AttemptRepository,
        answer_repository: AnswerRepository,
        question_repository: QuestionRepository,
        scoring_service: ScoringService,
        scheduler: AsyncIOScheduler,
    ) -> None:
        self._attempts = attempt_repository
        self._answers = answer_repository
        self._questions = question_repository
        self._scoring = scoring_service
        self._scheduler = scheduler

    # ---------- start ----------

    async def start(self, user: User, test: Test) -> Attempt:
        """Create a fresh ``in_progress`` attempt and schedule its timer jobs.

        Raises:
            NotApprovedError: user hasn't paid / been approved yet.
            AttemptAlreadyExistsError: user already took this test; the
                handler should show the prior result instead.
        """
        if user.status != "approved":
            raise NotApprovedError()

        # One open attempt at a time. A second one can otherwise be created
        # via a stale pre-test button that survives a publish (the old test
        # gets archived, the new one becomes active, and start() targets the
        # new test_id without seeing the still-open attempt on the old one).
        # Resume the open attempt instead (CODE_REVIEW C3).
        open_attempt = await self._attempts.get_in_progress_for_user(user.id)
        if open_attempt is not None:
            raise AttemptAlreadyExistsError(open_attempt.id)

        existing = await self._attempts.get_by_user_and_test(user.id, test.id)
        if existing is not None:
            raise AttemptAlreadyExistsError(existing.id)

        duration_seconds = int(test.duration_seconds)
        started_at = now_utc()
        expires_at = started_at + timedelta(seconds=duration_seconds)

        try:
            attempt = await self._attempts.create(
                user_id=user.id,
                test_id=test.id,
                started_at=started_at,
                expires_at=expires_at,
            )
        except IntegrityError as exc:
            # Lost the race to a concurrent "Начать" tap: the unique
            # (user_id, test_id) index rejected the second insert. The flush
            # error has poisoned the session for further reads, so we can't
            # fetch the existing attempt id here — surface the friendly
            # "already attempted" message and let the request roll back
            # (CODE_REVIEW M1).
            raise AttemptAlreadyExistsError() from exc

        self._schedule_attempt_jobs(attempt)

        logger.info(
            "attempt_started",
            attempt_id=attempt.id,
            user_id=user.id,
            test_id=test.id,
            expires_at=expires_at.isoformat(),
        )
        return attempt

    # ---------- read ----------

    async def get_user_attempt_for_test(self, user_id: int, test_id: int) -> Attempt | None:
        """Return the user's existing attempt for this test, if any."""
        return await self._attempts.get_by_user_and_test(user_id, test_id)

    async def get_in_progress_attempt(self, user_id: int) -> Attempt | None:
        """Return the user's open attempt on any test, or ``None``.

        Lets the entry flow resume an attempt that's still in progress on a
        now-archived test (PRODUCT_BLUEPRINT §13) before considering the
        active test (CODE_REVIEW C3).
        """
        return await self._attempts.get_in_progress_for_user(user_id)

    async def finalize_in_progress_for_user(self, user_id: int) -> int:
        """Force-expire every open attempt for a user and cancel its timer jobs.

        Used when an admin bans a user mid-test (CODE_REVIEW H20): without
        this the attempt sits ``in_progress`` and its scheduled warning /
        auto-submit jobs keep DMing a now-banned user. Reuses the idempotent
        ``finish`` so scoring + job cancellation are handled consistently.
        Returns the number of attempts finalized.
        """
        attempts = await self._attempts.list_in_progress_for_user(user_id)
        for attempt in attempts:
            await self.finish(attempt.id, reason="expired")
        return len(attempts)

    async def get_attempt(self, attempt_id: int) -> Attempt | None:
        """Fetch a raw attempt row by id (no ownership check)."""
        return await self._attempts.get_by_id(attempt_id)

    async def get_attempt_for_user(self, attempt_id: int, user_id: int) -> Attempt | None:
        """Fetch an attempt only if it belongs to ``user_id`` (else ``None``).

        Ownership-checked counterpart to :meth:`get_attempt` so callers like
        the result screen can't render another user's attempt from a guessed
        id (CODE_REVIEW M4).
        """
        attempt = await self._attempts.get_by_id(attempt_id)
        if attempt is None or attempt.user_id != user_id:
            return None
        return attempt

    async def list_in_progress(self) -> list[Attempt]:
        """All ``in_progress`` attempts — used by the startup reconciliation sweep."""
        return await self._attempts.list_in_progress()

    async def list_expired_in_progress(self, now: datetime) -> list[int]:
        """Attempt IDs still ``in_progress`` but past ``expires_at``.

        Feeds the recurring safety-net sweep that finalizes attempts whose
        per-attempt expire job was lost (CODE_REVIEW C4 / ARCHITECTURE_SPEC
        §10.15).
        """
        return await self._attempts.list_expired_in_progress(now)

    async def list_finished_for_user(
        self,
        user_id: int,
        *,
        limit: int = 50,
    ) -> list[tuple[Attempt, Test]]:
        """Past finished attempts for one user (newest first), joined with their test row.

        Feeds the «📜 Мои результаты» button in the main menu.
        """
        return await self._attempts.list_finished_with_test_for_user(user_id, limit=limit)

    async def count_by_status(self) -> dict[str, int]:
        """``{status: count}`` across all attempts — feeds /stats."""
        return await self._attempts.count_by_status()

    async def list_top_for_test(
        self,
        test_id: int,
        *,
        limit: int = 20,
    ) -> list[LeaderboardEntry]:
        """Top ``limit`` finished attempts for a test, ordered by score then finish-time."""
        return await self._attempts.list_top_for_test(test_id, limit=limit)

    async def get_attempt_detail(self, attempt_id: int) -> AttemptDetail | None:
        """Bundle the attempt + its questions + its answers for the admin /attempt command."""
        attempt = await self._attempts.get_by_id(attempt_id)
        if attempt is None:
            return None
        questions = await self._questions.list_by_test(attempt.test_id)
        answers = await self._answers.list_by_attempt(attempt_id)
        return AttemptDetail(
            attempt=attempt,
            questions=tuple(questions),
            answers_by_question_id={a.question_id: a for a in answers},
        )

    async def get_state(self, attempt_id: int, user_id: int) -> AttemptState:
        """Build the DTO the test-screen view needs.

        Verifies the attempt belongs to ``user_id`` so a guessed
        ``attempt_id`` from a callback_data can't leak another user's
        progress.
        """
        attempt = await self._attempts.get_by_id(attempt_id)
        if attempt is None or attempt.user_id != user_id:
            raise AttemptNotVisibleError()

        questions = await self._questions.list_by_test(attempt.test_id)
        answers = await self._answers.list_by_attempt(attempt_id)
        answers_by_question_id = {a.question_id: a for a in answers}

        time_remaining = max(0, int((attempt.expires_at - now_utc()).total_seconds()))

        return AttemptState(
            attempt_id=attempt.id,
            user_id=attempt.user_id,
            test_id=attempt.test_id,
            status=attempt.status,
            current_position=attempt.current_position,
            started_at=attempt.started_at,
            expires_at=attempt.expires_at,
            time_remaining_seconds=time_remaining,
            questions=tuple(questions),
            answers_by_question_id=answers_by_question_id,
        )

    # ---------- writes ----------

    async def submit_answer(
        self,
        attempt_id: int,
        *,
        user_id: int,
        question_position: int,
        selected_option: str,
    ) -> None:
        """Persist the user's pick for the question at ``question_position``."""
        attempt = await self._attempts.get_by_id(attempt_id)
        if attempt is None or attempt.user_id != user_id:
            raise AttemptNotVisibleError()
        if attempt.status != "in_progress":
            # User taps an option after the timer fired. Silently ignore —
            # the next refresh will pull them into the result screen.
            logger.info(
                "submit_answer_ignored_finished",
                attempt_id=attempt_id,
                status=attempt.status,
            )
            return

        question = await self._questions.get_by_test_position(attempt.test_id, question_position)
        if question is None:
            raise SystemError(
                f"no question at position {question_position} in test {attempt.test_id}"
            )

        option = selected_option.strip().upper()
        is_correct = option == question.correct_option

        await self._answers.upsert(
            attempt_id=attempt_id,
            question_id=question.id,
            selected_option=option,
            is_correct=is_correct,
        )

    async def set_current_position(
        self,
        attempt_id: int,
        *,
        user_id: int,
        position: int,
    ) -> None:
        """Move the user's cursor to ``position`` (persisted for resume-after-restart)."""
        attempt = await self._attempts.get_by_id(attempt_id)
        if attempt is None or attempt.user_id != user_id:
            raise AttemptNotVisibleError()
        if not 1 <= position <= 50:
            raise SystemError(f"position {position} out of range 1..50")
        await self._attempts.set_current_position(attempt_id, position)

    # ---------- finish (idempotent) ----------

    async def finish(self, attempt_id: int, *, reason: FinishReason) -> AttemptResult:
        """Score + persist + cancel jobs. Safe to call more than once.

        If the attempt is already in a finished state (the user tapped
        finish then the timer expired, or vice versa), we re-read the
        stored scores and return them — no recomputation, no UPDATE.
        """
        attempt = await self._attempts.get_by_id(attempt_id)
        if attempt is None:
            raise SystemError(f"attempt {attempt_id} not found")

        if attempt.status != "in_progress":
            # Already finalized — just hand the caller the persisted scores.
            # We did not own this finalization.
            return AttemptResult(
                attempt=attempt,
                scores=section_scores_from_attempt(attempt),
                owned_finalization=False,
            )

        answers = await self._answers.list_by_attempt(attempt_id)
        questions = await self._questions.list_by_test(attempt.test_id)
        scores = self._scoring.compute(answers, questions)

        repo_scores = AttemptScores(
            total=scores.total,
            rus_tili=scores.rus_tili,
            pedagogik=scores.pedagogik,
            kasbiy=scores.kasbiy,
        )
        target_status: Literal["submitted", "expired"] = (
            "submitted" if reason == "user" else "expired"
        )
        rowcount = await self._attempts.mark_finished(
            attempt_id, status=target_status, scores=repo_scores
        )

        # Cancel the still-pending timer jobs regardless of whether we
        # owned the finalize (the row may have been finalized between our
        # get_by_id and mark_finished by a racing tap/job).
        self._cancel_attempt_jobs(attempt_id)

        if rowcount == 0:
            # Someone else finalized between our get_by_id and the UPDATE;
            # re-read for the canonical scores. We did not own the write.
            refreshed = await self._attempts.get_by_id(attempt_id)
            assert refreshed is not None
            return AttemptResult(
                attempt=refreshed,
                scores=section_scores_from_attempt(refreshed),
                owned_finalization=False,
            )

        # Re-read with the new score columns populated. This call owned the
        # finalization (the guarded UPDATE flipped exactly this row).
        refreshed = await self._attempts.get_by_id(attempt_id)
        assert refreshed is not None
        logger.info(
            "attempt_finished",
            attempt_id=attempt_id,
            reason=reason,
            score_total=scores.total,
        )
        return AttemptResult(attempt=refreshed, scores=scores, owned_finalization=True)

    # ---------- warning slot claim (used by attempt_timer jobs) ----------

    async def claim_warning_slot(
        self,
        attempt_id: int,
        slot: WarningSlot,
    ) -> Attempt | None:
        """Atomically claim a warning-dispatch slot for an attempt.

        Returns the attempt iff the caller now owns the dispatch. Returns
        ``None`` when:

        * the attempt doesn't exist or is no longer ``in_progress`` (the
          expire job fired first, or the user submitted manually);
        * the warning slot is already stamped (another worker raced us,
          or a reconciliation re-fire happened after the original send).

        The ``warning_<slot>_sent_at`` UPDATE is the atomic point — its
        WHERE clause now also requires ``status = 'in_progress'``, so a
        non-zero rowcount means both "we won the slot" and "the attempt is
        still live". No pre-SELECT is needed (it was a TOCTOU window —
        CODE_REVIEW C7).
        """
        rowcount = await self._attempts.mark_warning_sent(attempt_id, slot)
        if rowcount == 0:
            return None
        # Re-read so the caller has the row (for user_id) and sees the stamp.
        return await self._attempts.get_by_id(attempt_id)

    # ---------- scheduler glue ----------

    def _schedule_attempt_jobs(self, attempt: Attempt) -> None:
        schedule_attempt_jobs(self._scheduler, attempt)

    def _cancel_attempt_jobs(self, attempt_id: int) -> None:
        cancel_attempt_jobs(self._scheduler, attempt_id)
