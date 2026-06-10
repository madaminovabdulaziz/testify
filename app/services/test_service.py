"""Excel-driven test lifecycle: draft → publish → archive → cancel.

The publish path is the load-bearing one — "exactly one ``active`` test
at a time" (PRODUCT_BLUEPRINT §9.3) is a hard invariant. We get it by
running the archive-of-the-old and the activation-of-the-new on the
same ``AsyncSession`` so the outer-request transaction makes them
atomic. If the DB rejects the new activation (e.g. the draft was
cancelled out from under us), the archive rolls back with it.

The broadcast that follows ``publish_notify`` is intentionally NOT
issued by this service — it requires a Telegram ``Bot`` and a fresh
session-bound user list. The handler orchestrates it via
:class:`~app.services.notification_service.NotificationService` after
``publish`` returns.
"""

from __future__ import annotations

from dataclasses import replace

import structlog
from sqlalchemy.exc import IntegrityError

from app.exceptions import DraftRequiredError, PublishConflictError, TestParseError
from app.models.question import Question
from app.models.test import Test
from app.repositories.question_repository import QuestionDraft, QuestionRepository
from app.repositories.test_repository import TestListEntry, TestRepository
from app.services.excel_parser import ExcelParser, ParsedTest
from app.services.question_validation import validate_test_completeness
from app.utils.datetime import now_utc

logger = structlog.get_logger()

DEFAULT_TEST_DURATION_SECONDS = 3200


class TestService:
    """Drive the ``tests`` + ``questions`` writes for the admin authoring flow."""

    # Tell pytest this isn't a unittest-style test class — the ``Test*``
    # python_classes pattern would otherwise match when test modules
    # import ``TestService`` into their namespace.
    __test__ = False

    def __init__(
        self,
        test_repository: TestRepository,
        question_repository: QuestionRepository,
        excel_parser: ExcelParser,
        *,
        default_duration_seconds: int = DEFAULT_TEST_DURATION_SECONDS,
    ) -> None:
        self._tests = test_repository
        self._questions = question_repository
        self._parser = excel_parser
        self._default_duration = default_duration_seconds

    # ---------- draft authoring ----------

    async def create_draft_from_excel(
        self,
        file_bytes: bytes,
        uploaded_by_admin_id: int | None,
        *,
        title: str | None = None,
    ) -> Test:
        """Parse the uploaded ``.xlsx``, persist a draft test + 50 questions.

        Raises:
            TestParseError: validation failed. Carries the line-referenced
                error list from the parser so the admin sees every issue.
        """
        parsed = self._parser.parse(file_bytes)
        if not isinstance(parsed, ParsedTest):
            errors = [(e.line, e.message) for e in parsed]
            raise TestParseError(errors)

        test_title = title or _default_title()
        test = await self._tests.create_draft(
            title=test_title,
            duration_seconds=self._default_duration,
            created_by_admin_id=uploaded_by_admin_id,
        )

        drafts = [
            QuestionDraft(
                section=q.section,
                position=q.position,
                question_text=q.question_text,
                option_a=q.option_a,
                option_b=q.option_b,
                option_c=q.option_c,
                option_d=q.option_d,
                correct_option=q.correct_option,
                has_image=q.has_image,
            )
            for q in parsed.questions
        ]
        await self._questions.bulk_create(test.id, drafts)

        logger.info(
            "test_draft_created",
            test_id=test.id,
            title=test_title,
            admin_id=uploaded_by_admin_id,
        )
        return test

    async def cancel_draft(self, draft_id: int) -> bool:
        """Hard-delete a draft test (its questions cascade). Returns whether anything was removed."""
        rowcount = await self._tests.delete_draft(draft_id)
        return rowcount > 0

    # ---------- draft authoring (web panel) ----------

    async def create_empty_draft(
        self,
        created_by_admin_id: int | None,
        *,
        title: str | None = None,
    ) -> Test:
        """Insert a draft test row with zero questions — the web editor's starting point."""
        test = await self._tests.create_draft(
            title=title or _default_title(),
            duration_seconds=self._default_duration,
            created_by_admin_id=created_by_admin_id,
        )
        logger.info("empty_draft_created", test_id=test.id, admin_id=created_by_admin_id)
        return test

    async def update_title(self, draft_id: int, title: str) -> bool:
        """Rename a draft. Returns False when the test is not a draft.

        Raises:
            ValueError: empty or over-long title (the form should have
                caught it; this is the service-level backstop).
        """
        cleaned = title.strip()
        if not cleaned or len(cleaned) > 200:
            raise ValueError("title must be 1-200 characters")
        return await self._tests.update_title(draft_id, cleaned) > 0

    async def replace_draft_questions(self, draft_id: int, drafts: list[QuestionDraft]) -> None:
        """Wholesale-replace a draft's questions (web editor save).

        Already-attached image file_ids are carried over by position when the
        question keeps ``has_image`` — so re-saving text edits doesn't force
        the admin to re-upload illustrations. Runs entirely in the caller's
        session, so the delete + re-insert commit (or roll back) as one unit.

        Raises:
            DraftRequiredError: the test is missing or no longer a draft.
        """
        test = await self._tests.get_by_id(draft_id)
        if test is None or test.status != "draft":
            raise DraftRequiredError()

        old_images = await self._questions.map_images_by_position(draft_id)
        await self._questions.delete_by_test(draft_id)

        carried = [
            replace(
                d,
                image_file_id=old_images[d.position][0],
                image_file_unique_id=old_images[d.position][1],
            )
            if d.has_image and d.position in old_images
            else d
            for d in drafts
        ]
        await self._questions.bulk_create(draft_id, carried)
        logger.info(
            "draft_questions_replaced",
            test_id=draft_id,
            question_count=len(drafts),
            images_carried=sum(1 for d in carried if d.image_file_id is not None),
        )

    async def validate_for_publish(self, test_id: int) -> list[str]:
        """Human-readable Russian blockers for the publish button; empty == publishable."""
        questions = await self._questions.list_by_test(test_id)
        blockers = validate_test_completeness(questions)
        missing = await self._questions.list_missing_image_positions(test_id)
        if missing:
            blockers.append(
                "Нет изображения для вопросов: " + ", ".join(str(p) for p in missing) + "."
            )
        return blockers

    async def duplicate_to_draft(
        self,
        source_test_id: int,
        created_by_admin_id: int | None,
    ) -> Test:
        """Copy any test (active/archived/draft) into a fresh editable draft.

        Question images come along — Telegram file_ids are bot-scoped, not
        message- or test-scoped, so reusing them across tests is free.

        Raises:
            ValueError: the source test does not exist.
        """
        source = await self._tests.get_by_id(source_test_id)
        if source is None:
            raise ValueError(f"test {source_test_id} not found")

        new_draft = await self.create_empty_draft(
            created_by_admin_id,
            title=f"{source.title} (копия)"[:200],
        )
        rows = await self._questions.list_by_test(source_test_id)
        drafts = [
            QuestionDraft(
                section=q.section,
                position=q.position,
                question_text=q.question_text,
                option_a=q.option_a,
                option_b=q.option_b,
                option_c=q.option_c,
                option_d=q.option_d,
                correct_option=q.correct_option,
                has_image=q.has_image,
                image_file_id=q.image_file_id,
                image_file_unique_id=q.image_file_unique_id,
            )
            for q in rows
        ]
        await self._questions.bulk_create(new_draft.id, drafts)
        logger.info(
            "test_duplicated",
            source_test_id=source_test_id,
            new_draft_id=new_draft.id,
            admin_id=created_by_admin_id,
        )
        return new_draft

    # ---------- image collection (authoring) ----------

    async def pending_image_positions(self, test_id: int) -> list[int]:
        """Positions flagged ``has_image`` that still need a photo, in order.

        The authoring handler prompts the teacher for these one at a time
        after the Excel upload (PRODUCT_BLUEPRINT §8.4 — extended for images).
        """
        return await self._questions.list_missing_image_positions(test_id)

    async def count_image_questions(self, test_id: int) -> int:
        """How many of the test's questions are flagged to carry an image."""
        return await self._questions.count_with_images(test_id)

    async def attach_question_image(
        self,
        test_id: int,
        position: int,
        *,
        file_id: str,
        file_unique_id: str,
    ) -> bool:
        """Attach a Telegram image to the question at ``position``.

        Returns whether a row was updated (False if the position isn't an
        image question — a stale tap). We persist only Telegram's identifiers;
        the bytes never touch our storage (ARCHITECTURE_SPEC §21.5).
        """
        rowcount = await self._questions.set_image(
            test_id, position, file_id=file_id, file_unique_id=file_unique_id
        )
        return rowcount > 0

    # ---------- read ----------

    async def get_active_test(self) -> Test | None:
        """Return the single currently-active test, or ``None``."""
        return await self._tests.get_active()

    async def get_test(self, test_id: int) -> Test | None:
        """Fetch one test by id (admin /leaderboard / /attempt support)."""
        return await self._tests.get_by_id(test_id)

    async def list_questions(self, test_id: int) -> list[Question]:
        """All questions of a test ordered by position (web editor render)."""
        return await self._questions.list_by_test(test_id)

    async def get_question(self, test_id: int, position: int) -> Question | None:
        """One question by (test, position) — image preview proxy support."""
        return await self._questions.get_by_test_position(test_id, position)

    async def count_by_status(self) -> dict[str, int]:
        """``{status: count}`` across the tests table — feeds /stats."""
        return await self._tests.count_by_status()

    async def list_recent(self, *, limit: int = 15) -> list[TestListEntry]:
        """Recent tests (newest first) with question + finished-attempt counts.

        Feeds the admin «🗂 Тесты» list — the surface that lets the teacher
        discover a ``test_id`` to pass to ``/leaderboard``.
        """
        return await self._tests.list_recent(limit=limit)

    # ---------- publish ----------

    async def publish(self, draft_id: int, *, notify: bool) -> Test:
        """Atomically archive the current active test and activate ``draft_id``.

        Both UPDATEs happen on the same ``AsyncSession``, so the outer
        request transaction (opened by ``DbSessionMiddleware``) gives us
        all-or-nothing semantics: if the new activation fails because the
        draft is no longer in ``draft`` state, the archive rolls back with
        it on the request's overall rollback.

        ``notify`` is recorded in the structured log so we can correlate
        "publish" with "broadcast" in audit traces. The actual broadcast
        is dispatched by the handler after this returns.

        Raises:
            ValueError: ``draft_id`` is not in ``draft`` status, so the
                activation UPDATE would have flipped zero rows.
            PublishConflictError: a concurrent publish already activated a
                different test; the DB's ``ux_tests__one_active`` index
                rejected this activation (CODE_REVIEW C8).
        """
        # Defense in depth: never publish a test whose image questions are
        # still missing their photo. The authoring flow only reaches the
        # publish buttons once collection is complete, so this guards against
        # a bug/race, not the happy path.
        missing = await self._questions.list_missing_image_positions(draft_id)
        if missing:
            raise ValueError(f"test {draft_id} has image questions without an image: {missing}")

        current_active = await self._tests.get_active()
        if current_active is not None and current_active.id != draft_id:
            archived = await self._tests.mark_archived(current_active.id)
            if archived != 1:
                # The previously-active row slipped out from under us.
                # Bail before we leave the system with zero active tests.
                raise ValueError(f"could not archive currently-active test {current_active.id}")

        try:
            activated = await self._tests.mark_active(draft_id)
        except IntegrityError as exc:
            # Another admin's publish committed an active test between our
            # get_active() above and this UPDATE; the unique index on
            # is_active_flag refused a second active row. Surfacing a
            # UserError makes the middleware roll back our half-done
            # transaction cleanly and shows the admin a retry message.
            raise PublishConflictError() from exc
        if activated != 1:
            raise ValueError(f"test {draft_id} is not in 'draft' status; cannot activate")

        # The status-guarded UPDATE already ran; ``get_by_id`` returns the
        # fresh row from the same session (post-flush).
        published = await self._tests.get_by_id(draft_id)
        assert published is not None

        logger.info(
            "test_published",
            test_id=draft_id,
            previous_active_id=current_active.id if current_active else None,
            notify=notify,
        )
        return published


def _default_title() -> str:
    """e.g. ``Тест от 2026-05-22`` per PRODUCT_BLUEPRINT §8.4 step 4."""
    return f"Тест от {now_utc().strftime('%Y-%m-%d')}"
