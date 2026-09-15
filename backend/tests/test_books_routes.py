"""Route tests for api/books.py's admin gate (2026-09-15 decision) and the
new GET ""/DELETE routes -- a documented exception to CLAUDE.md's "skip
route tests unless asked", same as auth/sessions: the thing worth checking
is the dependency wiring and the delete-cascade/blocking logic, not
ingest.py's pure functions (already covered by test_ingest.py).

Deliberately never exercises a real, non-dedup upload here: that would call
ingest_book() for real, loading bge-m3 and PyMuPDF -- out of place in a fast
route-test suite. The dedup path below returns before any of that runs.

Needs docker-compose's Postgres running and migrated (`alembic upgrade
head`) — every test here is marked "db"; `pytest -m "not db"` skips this
file entirely."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.book import Book, BookStatus
from app.models.chunk import Chunk, ChunkType
from app.models.session import Session
from app.models.user import User, UserRole
from app.pipeline.ingest import file_sha256

pytestmark = pytest.mark.db


async def _register(client, email: str, grade: int = 5) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "hunter22", "display_name": "Test", "grade": grade},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


async def _register_admin(client, db_session, email: str) -> str:
    """Registers a normal account, then flips its role on the db row --
    valid because require_admin (core/auth.py) reads user.role off the
    database, not the JWT claim. There is no API path to mint an admin
    (make_admin.py is the real bootstrap, exercised only manually)."""
    token = await _register(client, email)
    user = await db_session.scalar(select(User).where(User.email == email))
    user.role = UserRole.ADMIN
    await db_session.commit()
    return token


async def _make_book(db_session, *, file_hash: str, grade: int = 5, status: BookStatus = BookStatus.READY) -> Book:
    book = Book(title="Test Book", grade=grade, status=status, chunk_count=1, file_hash=file_hash)
    db_session.add(book)
    await db_session.commit()
    await db_session.refresh(book)
    return book


async def _make_chunk(db_session, *, book_id) -> Chunk:
    chunk = Chunk(
        book_id=book_id,
        lesson_id="u1-s1",
        unit=1,
        lesson_no=1,
        lesson_title="Lesson One",
        page=1,
        type=ChunkType.PASSAGE,
        text="Once upon a time.",
        embedding=[0.0] * 1024,
        sparse={},
    )
    db_session.add(chunk)
    await db_session.commit()
    await db_session.refresh(chunk)
    return chunk


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_create_book_requires_auth(client):
    get_settings().allow_anonymous = False
    resp = await client.post(
        "/api/v1/books",
        files={"file": ("book.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"title": "Test Book", "grade": "5"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_book_requires_auth(client):
    get_settings().allow_anonymous = False
    resp = await client.get("/api/v1/books/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_book_requires_admin(client):
    token = await _register(client, "student-upload@example.com")
    resp = await client.post(
        "/api/v1/books",
        files={"file": ("book.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"title": "Test Book", "grade": "5"},
        headers=_auth(token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_list_books_requires_admin(client):
    token = await _register(client, "student-list@example.com")
    resp = await client.get("/api/v1/books", headers=_auth(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_book_requires_admin(client, db_session):
    token = await _register(client, "student-delete@example.com")
    book = await _make_book(db_session, file_hash="hash-delete-perm")
    resp = await client.delete(f"/api/v1/books/{book.id}", headers=_auth(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_book_dedup_returns_existing_ready_book(client, db_session):
    token = await _register_admin(client, db_session, "uploader@example.com")

    data = b"%PDF-1.4 same bytes every time"
    existing = Book(
        title="Already Ingested",
        grade=5,
        status=BookStatus.READY,
        chunk_count=42,
        file_hash=file_sha256(data),
    )
    db_session.add(existing)
    await db_session.commit()
    await db_session.refresh(existing)

    resp = await client.post(
        "/api/v1/books",
        files={"file": ("book.pdf", data, "application/pdf")},
        data={"title": "A different title, ignored on dedup", "grade": "8"},
        headers=_auth(token),
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["id"] == str(existing.id)
    assert body["status"] == "ready"
    assert body["chunk_count"] == 42


@pytest.mark.asyncio
async def test_get_book_authenticated(client, db_session):
    token = await _register(client, "getter@example.com")
    book = Book(title="Test Book", grade=5, status=BookStatus.PROCESSING, chunk_count=0, file_hash="get-book-hash")
    db_session.add(book)
    await db_session.commit()
    await db_session.refresh(book)

    resp = await client.get(f"/api/v1/books/{book.id}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"


@pytest.mark.asyncio
async def test_list_books_admin(client, db_session):
    token = await _register_admin(client, db_session, "lister@example.com")
    book = await _make_book(db_session, file_hash="hash-list-admin")

    resp = await client.get("/api/v1/books", headers=_auth(token))
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert str(book.id) in ids


@pytest.mark.asyncio
async def test_delete_book_not_found(client, db_session):
    token = await _register_admin(client, db_session, "deleter-404@example.com")
    resp = await client.delete("/api/v1/books/00000000-0000-0000-0000-000000000000", headers=_auth(token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_book_removes_book_and_chunks(client, db_session):
    token = await _register_admin(client, db_session, "deleter-ok@example.com")
    book = await _make_book(db_session, file_hash="hash-delete-ok")
    await _make_chunk(db_session, book_id=book.id)

    resp = await client.delete(f"/api/v1/books/{book.id}", headers=_auth(token))
    assert resp.status_code == 204

    assert await db_session.get(Book, book.id) is None
    remaining_chunks = await db_session.scalar(select(Chunk).where(Chunk.book_id == book.id))
    assert remaining_chunks is None


@pytest.mark.asyncio
async def test_delete_book_blocked_when_session_exists(client, db_session):
    token = await _register_admin(client, db_session, "deleter-blocked@example.com")
    book = await _make_book(db_session, file_hash="hash-delete-blocked")
    session_owner = await db_session.scalar(select(User).where(User.email == "deleter-blocked@example.com"))
    db_session.add(Session(user_id=session_owner.id, book_id=book.id, grade=5))
    await db_session.commit()

    resp = await client.delete(f"/api/v1/books/{book.id}", headers=_auth(token))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BOOK_HAS_SESSIONS"
    assert await db_session.get(Book, book.id) is not None


@pytest.mark.asyncio
async def test_delete_book_blocked_when_only_soft_deleted_session_exists(client, db_session):
    """A soft-deleted session row still points at this book (CLAUDE.md:
    sessions are soft-deleted, never hard-deleted), so it must still block
    the book delete -- otherwise the row would be left dangling on a
    ForeignKeyViolationError, or worse, silently orphaned if the FK were
    ever relaxed."""
    token = await _register_admin(client, db_session, "deleter-soft@example.com")
    book = await _make_book(db_session, file_hash="hash-delete-soft")
    session_owner = await db_session.scalar(select(User).where(User.email == "deleter-soft@example.com"))
    db_session.add(
        Session(user_id=session_owner.id, book_id=book.id, grade=5, deleted_at=datetime.now(timezone.utc))
    )
    await db_session.commit()

    resp = await client.delete(f"/api/v1/books/{book.id}", headers=_auth(token))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BOOK_HAS_SESSIONS"
