"""Route tests for api/books.py's new auth gate (Phase 7 module 3) — a
documented exception to CLAUDE.md's "skip route tests unless asked", same
as auth/sessions: the thing worth checking is the dependency wiring itself,
not ingest.py's pure functions (already covered by test_ingest.py).

Deliberately never exercises a real, non-dedup upload here: that would call
ingest_book() for real, loading bge-m3 and PyMuPDF -- out of place in a fast
route-test suite. The dedup path below returns before any of that runs.

Needs docker-compose's Postgres running and migrated (`alembic upgrade
head`) — every test here is marked "db"; `pytest -m "not db"` skips this
file entirely."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.models.book import Book, BookStatus
from app.pipeline.ingest import file_sha256

pytestmark = pytest.mark.db


async def _register(client, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "hunter22", "display_name": "Test", "grade": 5},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


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
async def test_create_book_dedup_returns_existing_ready_book(client, db_session):
    token = await _register(client, "uploader@example.com")

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
