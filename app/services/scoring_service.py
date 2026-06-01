"""Pure scoring math.

Takes the raw rows from :class:`AnswerRepository` and
:class:`QuestionRepository` and turns them into per-section and total
correct-answer counts. Lives in its own module so it's trivially unit-
testable without any DB or mocks (ARCHITECTURE_SPEC §17.1).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.models.answer import Answer
from app.models.question import Question


@dataclass(frozen=True)
class SectionScores:
    """Correct-answer counts per DTM section plus the grand total."""

    rus_tili: int
    pedagogik: int
    kasbiy: int
    total: int


def section_scores_from_attempt(attempt: Any) -> SectionScores:
    """Rebuild :class:`SectionScores` from a finished attempt's stored columns.

    Shared by the attempt service and the test-taking handler (CODE_REVIEW N5
    — previously duplicated in both). ``attempt`` only needs the
    ``score_*_correct`` attributes, so it is duck-typed.
    """
    return SectionScores(
        rus_tili=int(attempt.score_rus_tili_correct or 0),
        pedagogik=int(attempt.score_pedagogik_correct or 0),
        kasbiy=int(attempt.score_kasbiy_correct or 0),
        total=int(attempt.score_total_correct or 0),
    )


class ScoringService:
    """Stateless. Constructed without arguments and reused freely."""

    def compute(
        self,
        answers: Iterable[Answer],
        questions: Iterable[Question],
    ) -> SectionScores:
        """Count correct answers per section.

        Correctness is recomputed here from the question's *current*
        ``correct_option`` rather than trusting the denormalized
        ``answers.is_correct`` (CODE_REVIEW M11) — so the score reflects a
        single source of truth even if a question's key were ever changed
        out from under earlier answers.
        """
        question_by_id: dict[int, Question] = {q.id: q for q in questions}

        rus = ped = kas = 0
        for answer in answers:
            question = question_by_id.get(answer.question_id)
            # An answer whose question_id isn't in the supplied list is
            # silently skipped — shouldn't happen with consistent inputs, but
            # one bad row shouldn't corrupt the whole score.
            if question is None:
                continue
            if answer.selected_option != question.correct_option:
                continue
            if question.section == "rus_tili":
                rus += 1
            elif question.section == "pedagogik":
                ped += 1
            elif question.section == "kasbiy":
                kas += 1

        return SectionScores(
            rus_tili=rus,
            pedagogik=ped,
            kasbiy=kas,
            total=rus + ped + kas,
        )
