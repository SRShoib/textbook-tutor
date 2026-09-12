"""
What: shared fixtures for tests that need a real FastAPI app + Postgres
      connection (currently: the Phase 5 auth route tests). Pipeline tests
      keep using their own local monkeypatch-only fixtures — nothing here
      is required by test_graph.py, test_verify.py, etc.

Why a transaction-rollback session instead of a separate test database:
      backend/tests currently has zero DB-touching tests, so there's no
      existing test-DB setup to reuse, and CLAUDE.md rules out a second
      database. Opening one connection, beginning a transaction, and
      binding the Session to it with join_transaction_mode="create_savepoint"
      means every route's own `await db.commit()` lands on a SAVEPOINT
      instead of the real transaction — then rolling the outer transaction
      back after the test undoes everything, against the same dev database
      and schema `alembic upgrade head` already created.

Why db_session opens its own engine instead of importing the shared one
      from app/core/db.py: that module-level engine is created once and
      pools asyncpg connections across whichever event loop happens to be
      running when a connection is first opened. pytest-asyncio (strict
      mode, function-scoped loops — the project default, unchanged here)
      gives each test its own event loop, so a connection pooled by one
      test's loop breaks when a later test's loop tries to reuse or close
      it (asyncpg connections are loop-bound). A NullPool engine scoped to
      the fixture sidesteps this entirely: every checkout opens a fresh
      connection in whatever loop is currently running and nothing is
      reused across tests.

Why these tests are marked "db" rather than just being normal tests: they
      need docker-compose's Postgres running and migrated. `pytest -m "not
      db"` keeps the existing 148 tests (all monkeypatch, no DB) runnable
      exactly as before, with Docker stopped.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import get_db
from app.main import app


@pytest.fixture(autouse=True)
def restore_settings():
    """Generic version of the fixture test_graph.py etc. each define
    locally with a fixed field list — this one snapshots every field so new
    auth tests can freely mutate jwt_secret, allow_anonymous,
    access_token_minutes, refresh_token_days, login_rate_limit_* without
    adding a new key here every time. A module that defines its own
    same-named fixture overrides this one; both styles coexist fine."""
    from app.core.config import get_settings

    settings = get_settings()
    original = dict(vars(settings))
    yield
    for key, value in original.items():
        setattr(settings, key, value)


@pytest_asyncio.fixture
async def db_session():
    test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with test_engine.connect() as conn:
            await conn.begin()
            session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
            try:
                yield session
            finally:
                await session.close()
                await conn.rollback()
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
