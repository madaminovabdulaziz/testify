"""Shared pytest fixtures.

Integration tests run against a real MySQL 8.4 container spun up by
``testcontainers`` (ARCHITECTURE_SPEC §17.4 — we deliberately do not
mock the ORM). The container is session-scoped to amortize its 30-60s
startup; per-test isolation is achieved with an outer connection-level
transaction that gets rolled back at teardown.

If Docker isn't reachable when the suite runs, every test that depends
on the ``session`` fixture is skipped with a clear reason rather than
erroring loudly — handy on CI runners that don't expose the docker
socket.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base


def _docker_available() -> bool:
    """Return True iff a usable Docker daemon is reachable."""
    try:
        import docker  # type: ignore[import-not-found]

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


_DOCKER_OK = _docker_available()


# NOTE: no custom ``event_loop`` fixture. pytest-asyncio 1.x manages the loop
# itself; the loop scope is pinned to "session" for both fixtures and tests via
# ``asyncio_default_*_loop_scope`` in pyproject.toml so the session-scoped
# engine/container and the per-test sessions share one loop.


@pytest.fixture(scope="session")
def mysql_container() -> Iterator[object]:
    """Spin up MySQL 8.4 once for the whole test session."""
    if not _DOCKER_OK:
        pytest.skip("Docker daemon not reachable; integration tests skipped.")

    from testcontainers.mysql import MySqlContainer

    container = MySqlContainer(
        "mysql:8.4",
        username="bot",
        password="botpass",
        dbname="attestation",
    )
    container.with_command(
        "--character-set-server=utf8mb4 "
        "--collation-server=utf8mb4_unicode_ci "
        "--default-time-zone=+00:00"
    )
    with container as running:
        yield running


@pytest_asyncio.fixture(scope="session")
async def db_engine(mysql_container: object) -> AsyncIterator[AsyncEngine]:
    """Async engine wired to the testcontainer; creates the schema once."""
    # ``get_connection_url`` returns a sync URL whose driver varies by
    # testcontainers version (4.14 emits a bare ``mysql://``, older releases
    # ``mysql+pymysql://``). Force the async driver we use in production by
    # swapping whatever scheme it chose — otherwise SQLAlchemy falls back to
    # the sync ``MySQLdb`` dialect and every integration test errors.
    sync_url: str = mysql_container.get_connection_url()  # type: ignore[attr-defined]
    async_url = "mysql+asyncmy://" + sync_url.split("://", 1)[1]

    engine = create_async_engine(async_url, pool_pre_ping=True, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Per-test ``AsyncSession`` wrapped in a roll-back-on-teardown transaction.

    Each test sees an empty database — anything the test inserts /
    updates is undone when the outer transaction is rolled back. This
    keeps the suite order-independent without paying the cost of
    re-creating the schema between tests.
    """
    async with db_engine.connect() as connection:
        trans = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            class_=AsyncSession,
            # Defensive isolation: today nothing under test calls
            # ``session.commit()`` (repos only flush; the middleware commits in
            # prod), so the outer-transaction rollback alone would isolate. But
            # if a future test or service ever commits, a plain commit bound to
            # this connection's transaction would deassociate it and leak rows
            # across tests. ``create_savepoint`` makes each commit release a
            # SAVEPOINT instead, so ``trans.rollback()`` still undoes everything
            # — the canonical SQLAlchemy 2.0 "join an external transaction" recipe.
            join_transaction_mode="create_savepoint",
        )
        session = factory()
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
