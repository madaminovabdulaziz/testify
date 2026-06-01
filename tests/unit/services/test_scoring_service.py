"""Unit tests for :class:`app.services.scoring_service.ScoringService`."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scoring_service import ScoringService, SectionScores


def _q(*, id: int, section: str, correct_option: str = "A") -> SimpleNamespace:
    return SimpleNamespace(id=id, section=section, correct_option=correct_option)


def _a(*, question_id: int, is_correct: bool) -> SimpleNamespace:
    # Correctness is recomputed by compare-to-correct_option now (M11), so we
    # encode it as an option match: correct → "A" (the _q default), else "B".
    return SimpleNamespace(question_id=question_id, selected_option="A" if is_correct else "B")


def test_empty_inputs_score_zero() -> None:
    svc = ScoringService()
    assert svc.compute([], []) == SectionScores(0, 0, 0, 0)


def test_section_counts_use_questions_mapping() -> None:
    svc = ScoringService()
    questions = [
        _q(id=1, section="rus_tili"),
        _q(id=2, section="rus_tili"),
        _q(id=36, section="pedagogik"),
        _q(id=46, section="kasbiy"),
    ]
    answers = [
        _a(question_id=1, is_correct=True),
        _a(question_id=2, is_correct=False),  # not counted
        _a(question_id=36, is_correct=True),
        _a(question_id=46, is_correct=True),
    ]
    result = svc.compute(answers, questions)
    assert result == SectionScores(rus_tili=1, pedagogik=1, kasbiy=1, total=3)


def test_answer_without_matching_question_is_silently_skipped() -> None:
    """Defensive: if an answer references an unknown question, don't crash; don't count it."""
    svc = ScoringService()
    questions = [_q(id=1, section="rus_tili")]
    answers = [
        _a(question_id=1, is_correct=True),
        _a(question_id=999, is_correct=True),  # unknown question
    ]
    result = svc.compute(answers, questions)
    assert result == SectionScores(rus_tili=1, pedagogik=0, kasbiy=0, total=1)


def test_unknown_section_is_silently_skipped() -> None:
    """An answer pointing to a question with a non-standard section is dropped."""
    svc = ScoringService()
    questions = [
        _q(id=1, section="rus_tili"),
        _q(id=2, section="other"),  # not one of the 3 known sections
    ]
    answers = [
        _a(question_id=1, is_correct=True),
        _a(question_id=2, is_correct=True),
    ]
    result = svc.compute(answers, questions)
    assert result == SectionScores(rus_tili=1, pedagogik=0, kasbiy=0, total=1)


def test_perfect_score() -> None:
    svc = ScoringService()
    questions = (
        [_q(id=i, section="rus_tili") for i in range(1, 36)]
        + [_q(id=i, section="pedagogik") for i in range(36, 46)]
        + [_q(id=i, section="kasbiy") for i in range(46, 51)]
    )
    answers = [_a(question_id=q.id, is_correct=True) for q in questions]
    result = svc.compute(answers, questions)
    assert result == SectionScores(rus_tili=35, pedagogik=10, kasbiy=5, total=50)


def test_correctness_recomputed_from_current_correct_option() -> None:
    # CODE_REVIEW M11: scoring compares selected_option to the question's
    # current correct_option, not a denormalized is_correct. Here the answer
    # selected "A" but the key is "C" → it must NOT count, regardless of any
    # stale is_correct flag.
    svc = ScoringService()
    questions = [_q(id=1, section="rus_tili", correct_option="C")]
    answers = [SimpleNamespace(question_id=1, selected_option="A", is_correct=True)]
    assert svc.compute(answers, questions) == SectionScores(0, 0, 0, 0)
