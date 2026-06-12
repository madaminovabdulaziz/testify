"""Unit tests for the test_screen + result_screen views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.bot.views.result_screen import (
    CANCEL_PRETEST_CALLBACK,
    CONFIRM_START_TEST_CALLBACK,
    render_finish_confirmation,
    render_pretest_screen,
    render_prior_result_screen,
    render_result_screen,
)
from app.bot.views.test_screen import render_test_screen
from app.services.attempt_service import AttemptState
from app.services.scoring_service import SectionScores


def _question(*, qid: int, position: int, section: str = "rus_tili") -> SimpleNamespace:
    return SimpleNamespace(
        id=qid,
        position=position,
        section=section,
        question_text=f"Q{position}?",
        option_a="A-option",
        option_b="B-option",
        option_c="C-option",
        option_d="D-option",
        correct_option="A",
    )


def _answer(*, qid: int, option: str = "A", correct: bool = True) -> SimpleNamespace:
    return SimpleNamespace(question_id=qid, selected_option=option, is_correct=correct)


def _attempt(**overrides) -> SimpleNamespace:
    base = {
        "id": 42,
        "user_id": 7,
        "test_id": 3,
        "status": "submitted",
        "current_position": 1,
        "started_at": datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 5, 24, 10, 35, tzinfo=UTC),
        "expires_at": datetime(2026, 5, 24, 10, 53, 20, tzinfo=UTC),
        "score_total_correct": 42,
        "score_rus_tili_correct": 30,
        "score_pedagogik_correct": 8,
        "score_kasbiy_correct": 4,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _attempt_state(
    *,
    current_position: int = 1,
    answered_qids: tuple[int, ...] = (),
    time_remaining: int = 3200,
) -> AttemptState:
    questions = tuple(_question(qid=q, position=q, section=_section_for(q)) for q in range(1, 51))
    answers = {qid: _answer(qid=qid) for qid in answered_qids}
    return AttemptState(
        attempt_id=42,
        user_id=7,
        test_id=3,
        status="in_progress",
        current_position=current_position,
        started_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC) + timedelta(seconds=time_remaining),
        time_remaining_seconds=time_remaining,
        questions=questions,
        answers_by_question_id=answers,  # type: ignore[arg-type]
    )


def _section_for(pos: int) -> str:
    if pos <= 35:
        return "rus_tili"
    if pos <= 45:
        return "pedagogik"
    return "kasbiy"


# ---------- test_screen ----------


def test_test_screen_header_contains_timer_position_and_section() -> None:
    rendered = render_test_screen(_attempt_state(current_position=5, time_remaining=2535))
    # 2535s == 42:15
    assert "42:15" in rendered.text
    assert "Вопрос 5/50" in rendered.text
    assert "Русский язык" in rendered.text  # section label for pos 5


def test_test_screen_html_escapes_question_text() -> None:
    state = _attempt_state(current_position=1)
    # Inject a question with HTML-significant characters at position 1.
    questions = list(state.questions)
    questions[0] = SimpleNamespace(
        id=1,
        position=1,
        section="rus_tili",
        question_text="<script>x</script>",
        option_a="<b>a</b>",
        option_b="b & c",
        option_c="c",
        option_d="d",
        correct_option="A",
    )
    state = AttemptState(
        attempt_id=state.attempt_id,
        user_id=state.user_id,
        test_id=state.test_id,
        status=state.status,
        current_position=state.current_position,
        started_at=state.started_at,
        expires_at=state.expires_at,
        time_remaining_seconds=state.time_remaining_seconds,
        questions=tuple(questions),
        answers_by_question_id=state.answers_by_question_id,
    )
    rendered = render_test_screen(state)
    assert "&lt;script&gt;" in rendered.text
    assert "<script>" not in rendered.text
    assert "&amp;" in rendered.text


def test_test_screen_grid_marks_current_and_answered() -> None:
    state = _attempt_state(current_position=5, answered_qids=(1, 2, 4))
    kb = render_test_screen(state).reply_markup
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "🔴5" in labels
    assert "1✅" in labels
    assert "2✅" in labels
    # Unanswered position has its bare number with no decoration.
    assert "3" in labels
    assert "10" in labels


def test_test_screen_keyboard_button_count_is_57() -> None:
    state = _attempt_state(current_position=1)
    kb = render_test_screen(state).reply_markup
    total = sum(len(row) for row in kb.inline_keyboard)
    # 4 options + 2 nav + 1 finish + 50 grid
    assert total == 57


def test_test_screen_has_no_section_legend_below_question() -> None:
    # The legend was removed by client request — the header line already
    # names the current section, so the block only added noise.
    rendered = render_test_screen(_attempt_state(current_position=1))
    assert "Русский язык (1–35)" not in rendered.text
    assert "Педагогическое мастерство (36–45)" not in rendered.text
    assert "Раздел:" in rendered.text  # the header still names the section


def _state_with_image_at(position: int, file_id: str) -> AttemptState:
    state = _attempt_state(current_position=position)
    questions = list(state.questions)
    q = questions[position - 1]
    questions[position - 1] = SimpleNamespace(
        id=q.id,
        position=q.position,
        section=q.section,
        question_text=q.question_text,
        option_a=q.option_a,
        option_b=q.option_b,
        option_c=q.option_c,
        option_d=q.option_d,
        correct_option=q.correct_option,
        image_file_id=file_id,
    )
    return AttemptState(
        attempt_id=state.attempt_id,
        user_id=state.user_id,
        test_id=state.test_id,
        status=state.status,
        current_position=state.current_position,
        started_at=state.started_at,
        expires_at=state.expires_at,
        time_remaining_seconds=state.time_remaining_seconds,
        questions=tuple(questions),
        answers_by_question_id=state.answers_by_question_id,
    )


def test_test_screen_image_question_renders_as_photo() -> None:
    rendered = render_test_screen(_state_with_image_at(5, "TG_FILE_ID_5"))
    # Photo mode: file id set, caption carries the header + question.
    assert rendered.photo_file_id == "TG_FILE_ID_5"
    assert "Вопрос 5/50" in rendered.text
    # Keyboard is unchanged — still the full 57-button layout.
    assert sum(len(row) for row in rendered.reply_markup.inline_keyboard) == 57


def test_test_screen_text_question_has_no_photo_file_id() -> None:
    rendered = render_test_screen(_attempt_state(current_position=1))
    assert rendered.photo_file_id is None


# ---------- pretest_screen ----------


def test_pretest_screen_has_start_and_back_buttons() -> None:
    rendered = render_pretest_screen()
    assert "Тест готов" in rendered.text
    cbs = [b.callback_data for row in rendered.reply_markup.inline_keyboard for b in row]
    assert CONFIRM_START_TEST_CALLBACK in cbs
    assert CANCEL_PRETEST_CALLBACK in cbs


# ---------- finish_confirmation ----------


def test_finish_confirmation_shows_counts() -> None:
    rendered = render_finish_confirmation(attempt_id=42, answered_count=37)
    assert "37 из 50" in rendered.text
    assert "13" in rendered.text  # unanswered


def test_finish_confirmation_zero_answered_says_50_unanswered() -> None:
    rendered = render_finish_confirmation(attempt_id=42, answered_count=0)
    assert "0 из 50" in rendered.text
    assert "50" in rendered.text


# ---------- result_screen ----------


def test_result_screen_includes_score_and_percentage() -> None:
    rendered = render_result_screen(
        _attempt(),
        SectionScores(rus_tili=30, pedagogik=8, kasbiy=4, total=42),
        group_invite_link="https://t.me/+invite",
    )
    assert "42/50" in rendered.text
    assert "84.0%" in rendered.text
    assert "30/35" in rendered.text
    assert "8/10" in rendered.text
    assert "4/5" in rendered.text


def test_result_screen_chat_button_omitted_without_link() -> None:
    rendered = render_result_screen(
        _attempt(),
        SectionScores(rus_tili=0, pedagogik=0, kasbiy=0, total=0),
        group_invite_link=None,
    )
    assert rendered.reply_markup is None


def test_result_screen_chat_button_present_with_link() -> None:
    rendered = render_result_screen(
        _attempt(),
        SectionScores(rus_tili=0, pedagogik=0, kasbiy=0, total=0),
        group_invite_link="https://t.me/+invite",
    )
    btn = rendered.reply_markup.inline_keyboard[0][0]
    assert btn.url == "https://t.me/+invite"


def test_result_screen_duration_uses_mmss() -> None:
    attempt = _attempt(
        started_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 5, 24, 10, 21, 30, tzinfo=UTC),
    )
    rendered = render_result_screen(
        attempt,
        SectionScores(rus_tili=0, pedagogik=0, kasbiy=0, total=0),
        group_invite_link=None,
    )
    assert "21:30" in rendered.text


def test_prior_result_screen_says_already_attempted() -> None:
    rendered = render_prior_result_screen(
        _attempt(),
        SectionScores(rus_tili=30, pedagogik=8, kasbiy=4, total=42),
        group_invite_link=None,
    )
    assert "уже проходили" in rendered.text
    assert "42/50" in rendered.text


def test_question_markup_renders_as_telegram_entities() -> None:
    state = _attempt_state(current_position=1)
    questions = list(state.questions)
    q = questions[0]
    questions[0] = SimpleNamespace(
        id=q.id,
        position=q.position,
        section=q.section,
        question_text="Какой **глагол** относится к __первому__ спряжению?",
        option_a="**Видеть**",
        option_b=q.option_b,
        option_c=q.option_c,
        option_d=q.option_d,
        correct_option=q.correct_option,
    )
    patched = AttemptState(
        attempt_id=state.attempt_id,
        user_id=state.user_id,
        test_id=state.test_id,
        status=state.status,
        current_position=state.current_position,
        started_at=state.started_at,
        expires_at=state.expires_at,
        time_remaining_seconds=state.time_remaining_seconds,
        questions=tuple(questions),
        answers_by_question_id=state.answers_by_question_id,
    )

    rendered = render_test_screen(patched)

    assert "<b>глагол</b>" in rendered.text
    assert "<i>первому</i>" in rendered.text
    assert "A. <b>Видеть</b>" in rendered.text
    assert "**" not in rendered.text
