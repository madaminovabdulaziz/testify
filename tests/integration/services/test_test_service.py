"""Integration test for :class:`TestService` against real MySQL.

Focuses on the publish-atomicity requirement: after ``publish(B)`` on
top of an already-active A, both rows must reflect their new statuses
within the same transaction (read by re-fetching post-flush).
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository
from app.repositories.question_repository import QuestionRepository
from app.repositories.test_repository import TestRepository
from app.services.excel_parser import ExcelParser
from app.services.test_service import TestService


def _valid_xlsx_bytes() -> bytes:
    """Build a 50-question .xlsx that passes the parser."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Questions"
    sheet.append(
        [
            "section",
            "position",
            "question_text",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "correct_option",
        ]
    )
    for pos in range(1, 36):
        sheet.append(["rus_tili", pos, f"Q{pos}", "A1", "B1", "C1", "D1", "A"])
    for pos in range(36, 46):
        sheet.append(["pedagogik", pos, f"Q{pos}", "A1", "B1", "C1", "D1", "B"])
    for pos in range(46, 51):
        sheet.append(["kasbiy", pos, f"Q{pos}", "A1", "B1", "C1", "D1", "C"])
    buf = BytesIO()
    workbook.save(buf)
    return buf.getvalue()


async def test_publish_archives_previous_active_in_same_transaction(
    session: AsyncSession,
) -> None:
    admins = AdminRepository(session)
    tests = TestRepository(session)
    questions = QuestionRepository(session)
    svc = TestService(tests, questions, ExcelParser())

    admin = await admins.create(telegram_id=900, role="owner", added_by_admin_id=None)

    # ---------- create + publish first test ----------
    test_a = await svc.create_draft_from_excel(_valid_xlsx_bytes(), admin.id, title="A")
    published_a = await svc.publish(test_a.id, notify=False)
    assert published_a.status == "active"
    assert published_a.published_at is not None
    assert await svc.get_active_test() is not None

    # ---------- second test: publishing it archives the first ----------
    test_b = await svc.create_draft_from_excel(_valid_xlsx_bytes(), admin.id, title="B")
    published_b = await svc.publish(test_b.id, notify=False)

    # Re-fetch both rows from the same session — they must reflect the
    # post-transaction state (B active, A archived).
    session.expunge_all()
    a_fresh = await tests.get_by_id(test_a.id)
    b_fresh = await tests.get_by_id(test_b.id)
    assert a_fresh is not None and a_fresh.status == "archived"
    assert a_fresh.archived_at is not None
    assert b_fresh is not None and b_fresh.status == "active"
    assert b_fresh.published_at is not None
    assert published_b.id == test_b.id

    # And: exactly one active test at a time (PRODUCT_BLUEPRINT §9.3).
    active = await svc.get_active_test()
    assert active is not None and active.id == test_b.id


async def test_cancel_draft_removes_the_row(session: AsyncSession) -> None:
    admins = AdminRepository(session)
    tests = TestRepository(session)
    questions = QuestionRepository(session)
    svc = TestService(tests, questions, ExcelParser())

    admin = await admins.create(telegram_id=901, role="owner", added_by_admin_id=None)
    draft = await svc.create_draft_from_excel(_valid_xlsx_bytes(), admin.id)

    assert await svc.cancel_draft(draft.id) is True
    assert await tests.get_by_id(draft.id) is None
