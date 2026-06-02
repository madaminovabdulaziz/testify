"""Synthetic load test — N concurrent students taking the same test.

PRODUCT_BLUEPRINT §17 acceptance criterion 12 (50-student simulated
load test without errors).

This is a **manual smoke test**: it requires a real MySQL + Redis stack
reachable via the standard env vars (DB_HOST / DB_PORT / DB_USER /
DB_PASSWORD / DB_NAME). It's not part of ``make test`` because it spins
up real connections.

What it does:

1. Creates a temporary admin + 50 approved users + 1 published test
   in a clearly-tagged Telegram-ID range so collisions with real data
   are impossible.
2. Concurrently invokes the full attempt lifecycle for all 50 users —
   each task answers every question deterministically, then calls
   ``finish(reason='user')``.
3. Verifies every attempt ended in ``status='submitted'`` with the
   expected score, then cleans up everything it created.
4. Prints a summary report.

Usage::

    python scripts/load_test.py [--users N] [--concurrency C] [--keep]

``--keep`` skips the post-run cleanup so the data is inspectable in
the DB.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from io import BytesIO

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openpyxl import Workbook
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import build_async_mysql_url
from app.models.admin import Admin
from app.models.attempt import Attempt
from app.models.test import Test
from app.models.user import User
from app.repositories.admin_repository import AdminRepository
from app.repositories.answer_repository import AnswerRepository
from app.repositories.attempt_repository import AttemptRepository
from app.repositories.question_repository import QuestionRepository
from app.repositories.test_repository import TestRepository
from app.repositories.user_repository import UserRepository
from app.services.attempt_service import AttemptService
from app.services.excel_parser import ExcelParser
from app.services.scoring_service import ScoringService
from app.services.test_service import TestService

# Telegram IDs in this range are reserved for load-test users so we can
# wipe them clean without touching real users. Pick an obviously-large
# value that no real Telegram account will ever land on.
_LOAD_TEST_TG_BASE = 999_000_000
_LOAD_TEST_ADMIN_TG = _LOAD_TEST_TG_BASE - 1


def _db_url_from_env() -> str:
    """Async MySQL URL from env — DATABASE_URL/MYSQL_URL or the discrete DB_* vars."""
    return build_async_mysql_url(os.environ)


def _valid_xlsx_bytes(correct: str = "A") -> bytes:
    wb = Workbook()
    sheet = wb.active
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
        sheet.append(["rus_tili", pos, f"Q{pos}", "a", "b", "c", "d", correct])
    for pos in range(36, 46):
        sheet.append(["pedagogik", pos, f"Q{pos}", "a", "b", "c", "d", correct])
    for pos in range(46, 51):
        sheet.append(["kasbiy", pos, f"Q{pos}", "a", "b", "c", "d", correct])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _seed(session_factory, *, n_users: int) -> tuple[int, int, list[int]]:
    """Create the admin, 1 published test, and N approved users.

    Returns (admin_id, test_id, [user_id, ...]).
    """
    async with session_factory() as session:
        # Wipe any leftover load-test rows from a prior aborted run.
        await _cleanup(session, n_users=n_users, commit=False)

        admins = AdminRepository(session)
        users = UserRepository(session)
        tests = TestRepository(session)
        questions = QuestionRepository(session)
        scheduler = AsyncIOScheduler()  # unused; passed for AttemptService construction
        test_service = TestService(tests, questions, ExcelParser())

        admin = await admins.create(
            telegram_id=_LOAD_TEST_ADMIN_TG,
            role="owner",
            added_by_admin_id=None,
        )

        draft = await test_service.create_draft_from_excel(
            _valid_xlsx_bytes(correct="A"),
            uploaded_by_admin_id=admin.id,
            title="Load test",
        )
        published = await test_service.publish(draft.id, notify=False)

        user_ids: list[int] = []
        for i in range(n_users):
            u = await users.create(
                telegram_id=_LOAD_TEST_TG_BASE + i,
                username=f"loaduser_{i}",
            )
            await users.set_name(u.id, f"Load User {i}")
            await users.mark_approved(u.id)
            user_ids.append(u.id)

        await session.commit()
        _ = scheduler  # keep mypy happy (created but unused at seed time)
        return admin.id, published.id, user_ids


async def _run_one_attempt(
    session_factory,
    *,
    user_id: int,
    test_id: int,
) -> tuple[int, str, int]:
    """Take the test as one user — returns (user_id, final_status, score)."""
    async with session_factory() as session:
        scheduler = AsyncIOScheduler()  # in-memory, never started; jobs noop
        users = UserRepository(session)
        attempt_service = AttemptService(
            AttemptRepository(session),
            AnswerRepository(session),
            QuestionRepository(session),
            ScoringService(),
            scheduler,
        )

        user = await users.get_by_id(user_id)
        if user is None:
            raise RuntimeError(f"user {user_id} not found")

        test = await TestRepository(session).get_by_id(test_id)
        if test is None:
            raise RuntimeError(f"test {test_id} not found")

        attempt = await attempt_service.start(user, test)
        await session.commit()

        # Answer every question with "A" (the correct answer for all 50).
        state = await attempt_service.get_state(attempt.id, user_id=user.id)
        for question in state.questions:
            await attempt_service.submit_answer(
                attempt.id,
                user_id=user.id,
                question_position=question.position,
                selected_option="A",
            )
        await session.commit()

        result = await attempt_service.finish(attempt.id, reason="user")
        await session.commit()

        return user_id, result.attempt.status, result.scores.total


async def _cleanup(session, *, n_users: int, commit: bool = True) -> None:
    """Wipe rows created by a previous (possibly aborted) load test."""
    tg_lo = _LOAD_TEST_ADMIN_TG
    tg_hi = _LOAD_TEST_TG_BASE + n_users

    # Find user_ids in the load-test range and delete their attempts
    # first (cascade handles answers).
    user_id_rows = await session.execute(
        select(User.id).where(User.telegram_id.between(tg_lo, tg_hi))
    )
    user_ids = [r[0] for r in user_id_rows]
    if user_ids:
        await session.execute(delete(Attempt).where(Attempt.user_id.in_(user_ids)))

    # Drop the load-test test row + cascade to questions.
    await session.execute(delete(Test).where(Test.title == "Load test"))

    # Drop the load-test users + admin.
    await session.execute(delete(User).where(User.telegram_id.between(tg_lo, tg_hi)))
    await session.execute(delete(Admin).where(Admin.telegram_id == _LOAD_TEST_ADMIN_TG))

    if commit:
        await session.commit()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Concurrent attempt-load smoke test")
    parser.add_argument("--users", type=int, default=50, help="Number of concurrent users")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=50,
        help="Max in-flight attempts (semaphore)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Don't clean up rows after the run",
    )
    args = parser.parse_args()

    db_url = _db_url_from_env()
    engine = create_async_engine(
        db_url,
        # Give the pool enough headroom for the configured concurrency
        # plus the seed/cleanup connection.
        pool_size=args.concurrency + 5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    print(f"▶ Connecting to {db_url.split('@', 1)[-1]} …")
    print(f"▶ Seeding 1 admin + {args.users} users + 1 published test …")
    started = time.monotonic()
    try:
        admin_id, test_id, user_ids = await _seed(session_factory, n_users=args.users)
    except Exception as exc:
        print(f"❌ Seed failed: {exc}")
        await engine.dispose()
        return 1
    seed_elapsed = time.monotonic() - started

    print(f"▶ Running {args.users} concurrent attempts (semaphore={args.concurrency}) …")
    sem = asyncio.Semaphore(args.concurrency)

    async def _bounded(uid: int) -> tuple[int, str, int] | tuple[int, str, str]:
        async with sem:
            try:
                return await _run_one_attempt(session_factory, user_id=uid, test_id=test_id)
            except Exception as exc:
                return (uid, "error", repr(exc))

    run_started = time.monotonic()
    results = await asyncio.gather(*(_bounded(uid) for uid in user_ids))
    run_elapsed = time.monotonic() - run_started

    successes = [r for r in results if r[1] == "submitted"]
    failures = [r for r in results if r[1] != "submitted"]
    scores = [int(r[2]) for r in successes]

    print()
    print("───── Load test report ─────")
    print(f"  users:          {args.users}")
    print(f"  concurrency:    {args.concurrency}")
    print(f"  seed duration:  {seed_elapsed:5.2f}s")
    print(f"  run duration:   {run_elapsed:5.2f}s")
    print(f"  submitted:      {len(successes)} / {args.users}")
    print(f"  errors:         {len(failures)}")
    if scores:
        all_correct = all(s == 50 for s in scores)
        print(f"  all-A score:    {scores[0]} (consistent: {all_correct})")
    if failures:
        for uid, status, info in failures[:5]:
            print(f"    ✗ user_id={uid} status={status} info={info}")

    # ---------- post-conditions ----------
    exit_code = 0
    if failures:
        print("❌ FAIL: some attempts errored out")
        exit_code = 2
    elif len(successes) != args.users:
        print("❌ FAIL: not every attempt landed in submitted")
        exit_code = 3
    elif scores and not all(s == 50 for s in scores):
        print("❌ FAIL: scores are not consistent")
        exit_code = 4
    else:
        print("✅ PASS: every attempt finished in submitted with consistent scores")

    if not args.keep:
        async with session_factory() as session:
            await _cleanup(session, n_users=args.users, commit=True)
        print("▶ Cleaned up load-test rows.")
    else:
        print(f"▶ Kept load-test data (admin_id={admin_id}, test_id={test_id}).")

    await engine.dispose()
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
